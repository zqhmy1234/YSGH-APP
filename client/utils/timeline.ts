/**
 * 时间轴客户端（B-F8-2）：GET /api/v1/events/timeline（level 过滤）
 *
 * 后端返回完整列表（第一波无服务端游标分页，契约保持 list）；
 * 客户端按 L1/L2 分组渲染 + 增量渲染（数量大时分批 append，保证首屏流畅）。
 * 服务端游标分页列入第二波（进度文档已记录）。
 */
import { get, dataArr } from './api'

export class TimelineEvent {
	id: string
	level: number
	title: string
	titleSource: string
	startTime: number // epoch ms
	endTime: number
	place: string
	/** 情绪（后端契约是 dict|null，如 {label, confidence}；P1-A 对齐改 getJSON） */
	emotion: UTSJSONObject | null
	photoCount: number
	contentCount: number
	/** draft / confirmed / rejected（B3-5；L2 待确认区按 status+confidence 分组） */
	status: string
	/** 置信度：<0.7 进"待确认"区（B3-5 阈值，与 B1 0.7 一致）；0 = 未知 */
	confidence: number
	/** 封面 content_id（无则首图回退，B3-4 封面展示） */
	coverContentId: string
	/** L1 卡片下的 L2 主题（客户端分组填充；接口层恒为空数组） */
	children: Array<TimelineEvent>
	/** 卡片照片条 content_id 列表（客户端会话填充：端侧聚合成员，按拍摄顺序） */
	photoIds: Array<string>
	/** 封面本地路径（cover_content_id 优先，无则首图回退；无本地映射则空串） */
	coverPath: string

	constructor(
		id: string,
		level: number,
		title: string,
		titleSource: string,
		startTime: number,
		endTime: number,
		place: string,
		emotion: UTSJSONObject | null,
		photoCount: number,
		contentCount: number,
		status: string,
		confidence: number,
		coverContentId: string
	) {
		this.id = id
		this.level = level
		this.title = title
		this.titleSource = titleSource
		this.startTime = startTime
		this.endTime = endTime
		this.place = place
		this.emotion = emotion
		this.photoCount = photoCount
		this.contentCount = contentCount
		this.status = status
		this.confidence = confidence
		this.coverContentId = coverContentId
		this.children = []
		this.photoIds = []
		this.coverPath = ''
	}
}

/** ISO8601 → epoch ms（UTS Date 字符串解析不可靠，手动拆解；兼容 Z 与 +08:00 偏移） */
export function parseIsoToMs(iso: string): number {
	const tIdx = iso.indexOf('T')
	if (tIdx < 0) {
		return 0
	}
	const datePart = iso.substring(0, tIdx)
	const rest = iso.substring(tIdx + 1)
	const d = datePart.split('-')
	if (d.length !== 3) {
		return 0
	}
	const year = parseInt(d[0])
	const month = parseInt(d[1])
	const day = parseInt(d[2])
	// 时间与偏移分离
	let timePart = rest
	let offsetMs = 0
	const zIdx = timePart.indexOf('Z')
	if (zIdx >= 0) {
		timePart = timePart.substring(0, zIdx)
	} else {
		// +08:00 / -05:30 尾偏移
		let signIdx = -1
		const plusIdx = timePart.lastIndexOf('+')
		const minusIdx = timePart.lastIndexOf('-')
		if (plusIdx > 8) {
			signIdx = plusIdx
		} else if (minusIdx > 8) {
			signIdx = minusIdx
		}
		if (signIdx >= 0) {
			const off = timePart.substring(signIdx)
			timePart = timePart.substring(0, signIdx)
			const neg = off.startsWith('-')
			const parts = off.substring(1).split(':')
			const oh = parseInt(parts[0])
			const om = parts.length > 1 ? parseInt(parts[1]) : 0
			const offTotal = (oh * 3600 + om * 60) * 1000
			offsetMs = neg ? -offTotal : offTotal
		}
	}
	const t = timePart.split(':')
	const hour = parseInt(t[0])
	const minute = t.length > 1 ? parseInt(t[1]) : 0
	let second = 0
	let milli = 0
	if (t.length > 2) {
		const secPart = t[2]
		const dotIdx = secPart.indexOf('.')
		if (dotIdx >= 0) {
			second = parseInt(secPart.substring(0, dotIdx))
			const frac = secPart.substring(dotIdx + 1)
			if (frac.length > 0) {
				milli = parseInt(frac.substring(0, 3))
			}
		} else {
			second = parseInt(secPart)
		}
	}
	const utc = Date.UTC(year, month - 1, day, hour, minute, second, milli)
	return utc - offsetMs
}

/** 拉取时间轴（level 过滤：null=全部 / 1=L1 / 2=L2）；失败返回空数组（永不 reject） */
export function fetchTimeline(level: number | null): Promise<Array<TimelineEvent>> {
	const path = level == null ? '/api/v1/events/timeline' : '/api/v1/events/timeline?level=' + level
	return new Promise<Array<TimelineEvent>>((resolve) => {
		get(path).then((body: UTSJSONObject | null) => {
			if (body == null) {
				resolve([])
				return
			}
			const arr = dataArr(body)
			const result: Array<TimelineEvent> = []
			for (let i = 0; i < arr.length; i++) {
				const item = arr[i]
				const ev = new TimelineEvent(
					item.getString('id') as string,
					item.getNumber('level') as number,
					item.getString('title') ?? '',
					item.getString('title_source') ?? '',
					parseIsoToMs(item.getString('start_time') ?? ''),
					parseIsoToMs(item.getString('end_time') ?? ''),
					item.getString('place') ?? '',
					item.getJSON('emotion'),
					item.getNumber('photo_count') as number,
					item.getNumber('content_count') as number,
					item.getString('status') ?? 'draft',
					item.getNumber('confidence') as number,
					item.getString('cover_content_id') ?? ''
				)
				result.push(ev)
			}
			resolve(result)
		})
	})
}

/** 日期分组键：epoch ms → yyyy-mm-dd（本地时区） */
export function dayKey(ms: number): string {
	const d = new Date(ms)
	const pad = (n: number): string => (n < 10 ? '0' + n : '' + n)
	return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
}

/** 友好日期标题：8月24日 · 周一（本地时区） */
export function friendlyDay(ms: number): string {
	const d = new Date(ms)
	const weeks: Array<string> = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
	return (d.getMonth() + 1) + '月' + d.getDate() + '日 · ' + weeks[d.getDay()]
}
