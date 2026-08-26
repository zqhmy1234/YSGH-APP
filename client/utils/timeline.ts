/**
 * 时间轴客户端（B-F8-2）：GET /api/v1/events/timeline（level 过滤）
 *
 * 后端返回完整列表（第一波无服务端游标分页，契约保持 list）；
 * 客户端按 L1/L2 分组渲染 + 增量渲染（数量大时分批 append，保证首屏流畅）。
 * 服务端游标分页列入第二波（进度文档已记录）。
 */
import { get, dataArr } from './api'
import { parseIsoMs } from './time'

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
