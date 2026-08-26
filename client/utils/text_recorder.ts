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
		console.log('[yishu] pollClassify tick ' + next + ' res=' + (res != null ? res.toJSONString() : 'null'))
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
