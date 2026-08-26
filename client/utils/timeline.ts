/**
 * 时间轴客户端（B-F8-2）：GET /api/v1/events/timeline（level 过滤）
 *
 * 后端返回完整列表（第一波无服务端游标分页，契约保持 list）；
 * 客户端按 L1/L2 分组渲染 + 增量渲染（数量大时分批 append，保证首屏流畅）。
 * 服务端游标分页列入第二波（进度文档已记录）。
 */
import { get, dataArr } from './api'
import { parseIsoMs, dayKey, friendlyDay } from './time'

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
					parseIsoMs(item.getString('start_time') ?? ''),
					parseIsoMs(item.getString('end_time') ?? ''),
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

/** 时间轴按日分组：日卡片组（L2 挂同日 L1 之下） */
export class DayGroup {
	key: string
	label: string
	events: Array<TimelineEvent>

	constructor(key: string, label: string, events: Array<TimelineEvent>) {
		this.key = key
		this.label = label
		this.events = events
	}
}

/** buildDayGroups 返回：日卡片组 + 待确认区事件（组件侧落 pendingEvents 状态） */
export class DayGroupResult {
	groups: Array<DayGroup>
	pending: Array<TimelineEvent>

	constructor(groups: Array<DayGroup>, pending: Array<TimelineEvent>) {
		this.groups = groups
		this.pending = pending
	}
}

/**
 * 按日分组（纯函数；原 index.uvue groupDays 迁移，F10 拆分行为等价）：
 * draft & confidence<0.7 → 待确认区；忽略事件过滤（isIgnored 注入，避免依赖 event_ops 本地态）；
 * L1/L2 各自按 startTime 倒序；L2 挂到同日 L1 之下，无同日 L1 则独立成组。
 */
export function buildDayGroups(events: Array<TimelineEvent>, isIgnored: (eventId: string) => boolean): DayGroupResult {
	const pending: Array<TimelineEvent> = []
	const l1: Array<TimelineEvent> = []
	const l2: Array<TimelineEvent> = []
	for (let i = 0; i < events.length; i++) {
		const ev = events[i]
		if (isIgnored(ev.id)) {
			continue // B3-5 忽略（本地隐藏）
		}
		const isPending = ev.status == 'draft' && ev.confidence > 0 && ev.confidence < 0.7
		if (isPending) {
			pending.push(ev)
			continue
		}
		if (ev.level === 1) {
			l1.push(ev)
		} else if (ev.level === 2) {
			l2.push(ev)
		}
	}
	pending.sort((a: TimelineEvent, b: TimelineEvent): number => b.startTime - a.startTime)
	l1.sort((a: TimelineEvent, b: TimelineEvent): number => b.startTime - a.startTime)
	l2.sort((a: TimelineEvent, b: TimelineEvent): number => b.startTime - a.startTime)

	const groups: Array<DayGroup> = []
	const l1ByDay: Map<string, number> = new Map<string, number>()
	for (let i = 0; i < l1.length; i++) {
		const ev = l1[i]
		const k = dayKey(ev.startTime)
		const copy = new TimelineEvent(
			ev.id,
			ev.level,
			ev.title,
			ev.titleSource,
			ev.startTime,
			ev.endTime,
			ev.place,
			ev.emotion,
			ev.photoCount,
			ev.contentCount,
			ev.status,
			ev.confidence,
			ev.coverContentId
		)
		groups.push(new DayGroup(k, friendlyDay(ev.startTime), [copy]))
		l1ByDay.set(k, groups.length - 1)
	}
	for (let j = 0; j < l2.length; j++) {
		const ev = l2[j]
		const k = dayKey(ev.startTime)
		const idx = l1ByDay.get(k)
		if (idx != null && groups[idx].events.length > 0) {
			groups[idx].events[0].children.push(ev)
		} else {
			groups.push(new DayGroup(k, friendlyDay(ev.startTime), [ev]))
			l1ByDay.set(k, groups.length - 1)
		}
	}
	return new DayGroupResult(groups, pending)
}
