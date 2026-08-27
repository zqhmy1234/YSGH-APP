/**
 * 文字记录 + 分类 + 纠错（T-TX-1/2）
 *
 * 链路（后端 P2-01 异步化）：
 *  POST /api/v1/contents        → 文字入库（status=processing）→ 返回 ContentOut
 *  POST /api/v1/classify        → 分类入队（SetFit ~27s 异步）→ {job_id}
 *  GET  /api/v1/classify/jobs/{id} → 轮询 → {status, result:{label,label_cn,confidence}}
 *  POST /api/v1/corrections     → 记录纠错（用户主动改标签）
 *  POST /api/v1/classify/arbitrate → 三层裁决入队 → {job_id}（T-TX-2）
 *  GET  /api/v1/classify/arbitrate/jobs/{id} → 轮询结果
 *
 * 约定：resolve-only（永不 reject），失败 resolve(null) + toast。
 */
import { post, get, dataObj } from './api'
// O19：全量响应（含用户 text）脱敏后再落日志——res 可能含提交文本，不得整段刷 logcat
import { redactLog } from './log'

export class ClassifyResult {
	label: string
	labelCn: string
	confidence: number
	scores: Array<ClassScore>

	constructor(label: string, labelCn: string, confidence: number, scores: Array<ClassScore>) {
		this.label = label
		this.labelCn = labelCn
		this.confidence = confidence
		this.scores = scores
	}
}

export class ClassScore {
	label: string
	labelCn: string
	score: number

	constructor(label: string, labelCn: string, score: number) {
		this.label = label
		this.labelCn = labelCn
		this.score = score
	}
}

/** 5 类标签中文名（与后端 VALID_CLASSES 对齐） */
const LABEL_CN: Map<string, string> = new Map<string, string>()
LABEL_CN.set('todo', '待办')
LABEL_CN.set('idea', '灵感')
LABEL_CN.set('emotion', '情绪')
LABEL_CN.set('quote', '引用')
LABEL_CN.set('mixed', '混合')

/** 标签选项（key 英文键 / cn 中文名；record.uvue 分类纠错标签条数据源） */
export class LabelOption {
	key: string
	cn: string

	constructor(key: string, cn: string) {
		this.key = key
		this.cn = cn
	}
}

/** 5 类标签全量选项（与后端 VALID_CLASSES 对齐；O10 标签双源收口——record.uvue data 引用之，不再页面硬编码） */
export const ALL_LABELS: Array<LabelOption> = [
	new LabelOption('todo', '待办'),
	new LabelOption('idea', '灵感'),
	new LabelOption('emotion', '情绪'),
	new LabelOption('quote', '引用'),
	new LabelOption('mixed', '混合')
]

export function labelCn(label: string): string {
	const v = LABEL_CN.get(label)
	return v != null ? v : label
}

/** POST /contents：文字入库（text 类型）；返回 content_id 或 null */
export function createTextContent(text: string): Promise<string | null> {
	const body: UTSJSONObject = {
		content_type: 'text',
		text: text,
		source: 'app'
	}
	return new Promise<string | null>((resolve) => {
		post('/api/v1/contents', body).then((res: UTSJSONObject | null) => {
			if (res == null) {
				resolve(null)
				return
			}
			const d = dataObj(res)
			if (d == null) {
				resolve(null)
				return
			}
			resolve(d.getString('id'))
		})
	})
}

/** POST /classify：分类入队；返回 job_id 或 null */
export function submitClassify(text: string): Promise<string | null> {
	const body: UTSJSONObject = {
		text: text
	}
	return new Promise<string | null>((resolve) => {
		post('/api/v1/classify', body).then((res: UTSJSONObject | null) => {
			if (res == null) {
				resolve(null)
				return
			}
			const d = dataObj(res)
			if (d == null) {
				resolve(null)
				return
			}
			resolve(d.getString('job_id'))
		})
	})
}

/** 轮询分类任务（最多 tries 次，间隔 intervalMs）；finished 返回结果，其余 null */
export function pollClassify(jobId: string, tries: number, intervalMs: number): Promise<ClassifyResult | null> {
	return new Promise<ClassifyResult | null>((resolve) => {
		pollClassifyTick(jobId, tries, intervalMs, 0, resolve)
	})
}

function pollClassifyTick(
	jobId: string,
	tries: number,
	intervalMs: number,
	count: number,
	done: (r: ClassifyResult | null) => void
): void {
	const next = count + 1
	get('/api/v1/classify/jobs/' + jobId).then((res: UTSJSONObject | null) => {
		// O19：全量响应脱敏后落日志（原整段 toJSONString 刷 logcat，含用户 text）
		console.log('[yishu] pollClassify tick ' + next + ' res=' + (res != null ? redactLog(res.toJSONString()) : 'null'))
		if (res == null) {
			done(null)
			return
		}
		const d = dataObj(res)
		if (d == null) {
			console.log('[yishu] pollClassify data null')
			done(null)
			return
		}
		const status = d.getString('status') ?? ''
		console.log('[yishu] pollClassify status="' + status + '" len=' + status.length + ' finished?=' + (status == 'finished'))
		if (status == 'finished') {
			console.log('[yishu] pollClassify finished hit')
			const r = d.getJSON('result')
			if (r != null) {
				done(parseClassifyResult(r))
				return
			}
			done(null)
			return
		}
		if (status == 'failed' || next >= tries) {
			done(null)
			return
		}
		setTimeout(() => {
			pollClassifyTick(jobId, tries, intervalMs, next, done)
		}, intervalMs)
	})
}

function parseClassifyResult(r: UTSJSONObject): ClassifyResult {
	const scores: Array<ClassScore> = []
	const arr = r.getArray('scores')
	if (arr != null) {
		for (let i = 0; i < arr.length; i++) {
			const item = arr[i] as UTSJSONObject
			scores.push(new ClassScore(item.getString('label') ?? '', item.getString('label_cn') ?? '', item.getNumber('score') as number))
		}
	}
	return new ClassifyResult(
		r.getString('label') ?? '',
		r.getString('label_cn') ?? '',
		r.getNumber('confidence') as number,
		scores
	)
}

/** POST /corrections：记录用户主动纠错（T-TX-2 数据源①）；返回是否成功 */
export function submitCorrection(contentId: string, text: string, newLabel: string, oldLabel: string): Promise<boolean> {
	const body: UTSJSONObject = {
		content_id: contentId,
		text: text,
		new_label: newLabel,
		old_label: oldLabel,
		source: 'active',
		content_type: 'text'
	}
	return new Promise<boolean>((resolve) => {
		post('/api/v1/corrections', body).then((res: UTSJSONObject | null) => {
			resolve(res != null)
		})
	})
}

/** POST /classify/arbitrate：三层裁决入队；返回 job_id 或 null */
export function submitArbitrate(text: string): Promise<string | null> {
	const body: UTSJSONObject = {
		text: text,
		content_type: 'text'
	}
	return new Promise<string | null>((resolve) => {
		post('/api/v1/classify/arbitrate', body).then((res: UTSJSONObject | null) => {
			if (res == null) {
				resolve(null)
				return
			}
			const d = dataObj(res)
			if (d == null) {
				resolve(null)
				return
			}
			resolve(d.getString('job_id'))
		})
	})
}

/** 轮询三层裁决任务；finished 返回最终 label（英文键）；其余 null */
export function pollArbitrate(jobId: string, tries: number, intervalMs: number): Promise<string | null> {
	return new Promise<string | null>((resolve) => {
		pollArbitrateTick(jobId, tries, intervalMs, 0, resolve)
	})
}

// ═══════════ US-25 连续纠错计数（C9 定稿：客户端本地实现，零后端改动）═══════════
// 存储：uni storage key=yishu_correction_streak（int，字符串化）+ last_ts；>72h 无纠错 → 清零
// 触发：连续纠错达 3 次 → record.uvue 弹"创建自定义分类？"（D4 拍板）；创建分类后 resetCorrectionStreak()
const CORRECTION_STREAK_KEY: string = 'yishu_correction_streak'
const CORRECTION_STREAK_TS_KEY: string = 'yishu_correction_streak_last_ts'
const CORRECTION_PROMPT_DISMISS_KEY: string = 'yishu_correction_prompt_dismissed'
/** 连续 3 天无纠错（72h）→ 计数清零 */
const STREAK_EXPIRE_MS: number = 72 * 60 * 60 * 1000
/** 触发阈值：连续纠错 3 次 */
const STREAK_PROMPT_THRESHOLD: number = 3

function readCorrectionStreak(): number {
	const raw = uni.getStorageSync(CORRECTION_STREAK_KEY) as string
	if (raw == null || raw == '') {
		return 0
	}
	const n = parseInt(raw)
	return isNaN(n) ? 0 : n
}

function readCorrectionLastTs(): number {
	const raw = uni.getStorageSync(CORRECTION_STREAK_TS_KEY) as string
	if (raw == null || raw == '') {
		return 0
	}
	const n = parseInt(raw)
	return isNaN(n) ? 0 : n
}

/**
 * 纠错成功后调用：读当前 streak → 距上次纠错 >72h 先清零（连续 3 天无纠错重置）→ 递增 → 写回（含 last_ts）。
 * @returns 递增后的 streak（调用方 ≥3 时按需弹提示，逻辑在 record.uvue）
 */
export function trackCorrectionStreak(): number {
	const now = Date.now()
	let streak = readCorrectionStreak()
	const lastTs = readCorrectionLastTs()
	if (lastTs > 0 && now - lastTs > STREAK_EXPIRE_MS) {
		streak = 0
	}
	streak = streak + 1
	uni.setStorageSync(CORRECTION_STREAK_KEY, '' + streak)
	uni.setStorageSync(CORRECTION_STREAK_TS_KEY, '' + now)
	return streak
}

/** 创建自定义分类 / 手动重置时调用：streak 与 last_ts 清零（避免反复弹） */
export function resetCorrectionStreak(): void {
	uni.setStorageSync(CORRECTION_STREAK_KEY, '0')
	uni.setStorageSync(CORRECTION_STREAK_TS_KEY, '0')
}

/**
 * 是否弹"创建自定义分类？"提示：streak ≥ 3 且未点"不再提示"。
 * （取消后 streak 保留 → 下次达 3 仍弹；如需"不再提示"调 dismissCorrectionPrompt，README/完成消息注明）
 */
export function shouldPromptCustomCategory(): boolean {
	const rawDismissed = uni.getStorageSync(CORRECTION_PROMPT_DISMISS_KEY)
	const dismissed = rawDismissed === '' ? false : (rawDismissed as boolean)
	if (dismissed === true) {
		return false
	}
	return readCorrectionStreak() >= STREAK_PROMPT_THRESHOLD
}

/** "不再提示"本地标记：调用后本机不再弹"创建自定义分类？"提示（D4 取消路径的可选增强） */
export function dismissCorrectionPrompt(): void {
	uni.setStorageSync(CORRECTION_PROMPT_DISMISS_KEY, true)
}

function pollArbitrateTick(
	jobId: string,
	tries: number,
	intervalMs: number,
	count: number,
	done: (r: string | null) => void
): void {
	const next = count + 1
	get('/api/v1/classify/arbitrate/jobs/' + jobId).then((res: UTSJSONObject | null) => {
		if (res == null) {
			done(null)
			return
		}
		const d = dataObj(res)
		if (d == null) {
			done(null)
			return
		}
		const status = d.getString('status') ?? ''
		if (status == 'finished') {
			const r = d.getJSON('result')
			if (r != null) {
				done(r.getString('label'))
				return
			}
			done(null)
			return
		}
		if (status == 'failed' || next >= tries) {
			done(null)
			return
		}
		setTimeout(() => {
			pollArbitrateTick(jobId, tries, intervalMs, next, done)
		}, intervalMs)
	})
}
