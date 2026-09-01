/**
 * 玩法层（T-PL-1/2/3）：回响卡片 + 冷启动访谈 + 消息中心
 *
 * 链路（后端契约）：
 *  GET  /api/v1/echo/today       → {content_id, content_type, text, taken_at, place, echo_date, fingerprint} | null
 *  POST /api/v1/echo/{id}/dismiss → {dismissed: true}
 *  GET  /api/v1/interview/questions → [{key, question} × 3]
 *  POST /api/v1/interview/answers  → {answers: {key: text}} → {dimensions, confirmation}
 *  GET  /api/v1/interview/profile  → 画像（冷启动状态）
 *  GET  /api/v1/messages          → Page{items:[MessageOut], cursor, has_more}
 *  POST /api/v1/messages/{id}/read → 单条已读
 *  POST /api/v1/messages/read-all  → 全部已读
 *
 * 约定：resolve-only（永不 reject），失败 resolve(null/[]/false) + toast。
 * O15 收口：端点路径统一走 contract.ts（PATH_*，与 OpenAPI 对齐）；O17：消息列表
 * 分页字段对齐（fetchMessagePage 返回 {items, cursor, hasMore}，未读数走游标累加）。
 */
import { get, post, dataObj, dataArr, showErrorToast } from './api'
import {
	PATH_MESSAGES,
	PATH_ECHO_TODAY,
	PATH_INTERVIEW_QUESTIONS,
	PATH_INTERVIEW_ANSWERS,
	PATH_INTERVIEW_PROFILE,
	parsePageData,
	PageData
} from './contract'

// ═══════════ 回响（T-PL-1）═══════════

export class EchoCard {
	contentId: string
	contentType: string
	text: string
	takenAt: string
	place: string
	echoDate: string

	constructor(contentId: string, contentType: string, text: string, takenAt: string, place: string, echoDate: string) {
		this.contentId = contentId
		this.contentType = contentType
		this.text = text
		this.takenAt = takenAt
		this.place = place
		this.echoDate = echoDate
	}
}

/** GET /echo/today：去年今日回响（无则 null） */
export function fetchTodayEcho(): Promise<EchoCard | null> {
	return new Promise<EchoCard | null>((resolve) => {
		get(PATH_ECHO_TODAY).then((res: UTSJSONObject | null) => {
			if (res == null) {
				resolve(null)
				return
			}
			const d = dataObj(res)
			if (d == null) {
				resolve(null)
				return
			}
			const contentId = d.getString('content_id')
			if (contentId == null || contentId == '') {
				resolve(null)
				return
			}
			resolve(
				new EchoCard(
					contentId,
					d.getString('content_type') ?? 'text',
					d.getString('text') ?? '',
					d.getString('taken_at') ?? '',
					d.getString('place') ?? '',
					d.getString('echo_date') ?? ''
				)
			)
		})
	})
}

/** POST /echo/{id}/dismiss：划掉（不再出现）；返回是否成功 */
export function dismissEcho(contentId: string): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		post('/api/v1/echo/' + contentId + '/dismiss', {}).then((res: UTSJSONObject | null) => {
			resolve(res != null)
		})
	})
}

// ═══════════ 冷启动访谈（T-PL-2）═══════════

export class InterviewQuestion {
	key: string
	question: string

	constructor(key: string, question: string) {
		this.key = key
		this.question = question
	}
}

export class InterviewResult {
	dimensions: string // 激活维度摘要
	confirmation: string // 复述确认文本

	constructor(dimensions: string, confirmation: string) {
		this.dimensions = dimensions
		this.confirmation = confirmation
	}
}

/** GET /interview/questions：三问；失败空数组 */
export function fetchInterviewQuestions(): Promise<Array<InterviewQuestion>> {
	return new Promise<Array<InterviewQuestion>>((resolve) => {
		get(PATH_INTERVIEW_QUESTIONS).then((res: UTSJSONObject | null) => {
			if (res == null) {
				resolve([])
				return
			}
			// data 直接是数组（后端 QUESTIONS 裸返回）：res.getArray('data') 取数组
			let arr: Array<UTSJSONObject> | null = null
			const raw = res.getArray('data')
			if (raw != null && raw.length > 0) {
				arr = raw as Array<UTSJSONObject>
			} else {
				// 兼容 {questions: [...]} 形态
				const d = dataObj(res)
				if (d != null) {
					const qArr = d.getArray('questions')
					if (qArr != null) {
						arr = qArr as Array<UTSJSONObject>
					}
				}
			}
			const result: Array<InterviewQuestion> = []
			if (arr != null) {
				for (let i = 0; i < arr.length; i++) {
					const item = arr[i] as UTSJSONObject
					result.push(new InterviewQuestion(item.getString('key') ?? '', item.getString('question') ?? ''))
				}
			}
			resolve(result)
		})
	})
}

/** 展平 {dim: [当前值]} → "relation_role：家人、伴侣；..." 摘要（P1-A 对齐：dimensions 是 dict） */
function flattenDimensions(dim: UTSJSONObject | null): string {
	if (dim == null) {
		return ''
	}
	const keys = UTSJSONObject.keys(dim)
	let summary = ''
	for (let i = 0; i < keys.length; i++) {
		const k = keys[i] as string
		const vals = dim.getArray(k) as Array<string> | null
		if (vals != null && vals.length > 0) {
			if (summary != '') {
				summary += '；'
			}
			summary += k + '：' + vals.join('、')
		}
	}
	return summary
}

/** POST /interview/answers：提交三问答案；返回复述确认或 null */
export function submitInterviewAnswers(answers: UTSJSONObject): Promise<InterviewResult | null> {
	const body: UTSJSONObject = {
		answers: answers
	}
	return new Promise<InterviewResult | null>((resolve) => {
		post(PATH_INTERVIEW_ANSWERS, body).then((res: UTSJSONObject | null) => {
			if (res == null) {
				resolve(null)
				return
			}
			const d = dataObj(res)
			if (d == null) {
				resolve(null)
				return
			}
			resolve(
				new InterviewResult(
					flattenDimensions(d.getJSON('dimensions')),
					d.getString('confirmation') ?? ''
				)
			)
		})
	})
}

/** GET /interview/profile：画像（冷启动状态）；返回 JSON 或 null */
export function fetchInterviewProfile(): Promise<UTSJSONObject | null> {
	return new Promise<UTSJSONObject | null>((resolve) => {
		get(PATH_INTERVIEW_PROFILE).then((res: UTSJSONObject | null) => {
			if (res == null) {
				resolve(null)
				return
			}
			resolve(dataObj(res))
		})
	})
}

// ═══════════ 消息中心（T-PL-3）═══════════

export class AppMessage {
	id: number
	channel: string
	msgType: string
	title: string
	body: string
	status: string
	sentAt: string

	constructor(id: number, channel: string, msgType: string, title: string, body: string, status: string, sentAt: string) {
		this.id = id
		this.channel = channel
		this.msgType = msgType
		this.title = title
		this.body = body
		this.status = status
		this.sentAt = sentAt
	}
}

/** 消息分页结果（O17：与后端 Page{items, cursor, has_more} 对齐） */
export class MessagePage {
	items: Array<AppMessage>
	cursor: string
	hasMore: boolean

	constructor(items: Array<AppMessage>, cursor: string, hasMore: boolean) {
		this.items = items
		this.cursor = cursor
		this.hasMore = hasMore
	}
}

/** GET /messages 分页拉取（status 过滤 ''=全部/unread/read；limit 每页条数）。
 *  O17：返回 cursor/has_more（不再丢弃分页契约字段）；失败空页（never reject） */
export function fetchMessagePage(status: string, cursor: string, limit: number): Promise<MessagePage> {
	// 后端 status 仅支持 unread/read/archived；'all' 视同全部（不传 status）
	let query = '?limit=' + limit
	if (status != '' && status != 'all') {
		query += '&status=' + status
	}
	if (cursor != '') {
		query += '&cursor=' + cursor
	}
	return new Promise<MessagePage>((resolve) => {
		get(PATH_MESSAGES + query).then((res: UTSJSONObject | null) => {
			const empty = new MessagePage([], '', false)
			if (res == null) {
				resolve(empty)
				return
			}
			const d = dataObj(res)
			if (d == null) {
				resolve(empty)
				return
			}
			const page = parsePageData(d)
			if (page == null) {
				resolve(empty)
				return
			}
			const result: Array<AppMessage> = []
			for (let i = 0; i < page.items.length; i++) {
				const item = page.items[i] as UTSJSONObject
				result.push(
					new AppMessage(
						item.getNumber('id') as number,
						item.getString('channel') ?? '',
						item.getString('msg_type') ?? '',
						item.getString('title') ?? '',
						item.getString('body') ?? '',
						item.getString('status') ?? '',
						item.getString('sent_at') ?? ''
					)
				)
			}
			resolve(new MessagePage(result, page.cursor, page.hasMore))
		})
	})
}

/** GET /messages 首屏（status 过滤 ''=全部/unread/read）；兼容旧调用方（取第一页 items）。
 *  分页消费请用 fetchMessagePage（O17：含 cursor/has_more）；失败空数组 */
export function fetchMessages(status: string): Promise<Array<AppMessage>> {
	return new Promise<Array<AppMessage>>((resolve) => {
		fetchMessagePage(status, '', 50).then((page: MessagePage) => {
			resolve(page.items)
		})
	})
}

/** 未读数（O17：不再被首屏 limit 截断——游标翻页累加 unread 条数；失败返回 0） */
export function fetchUnreadCount(): Promise<number> {
	return new Promise<number>((resolve) => {
		let total = 0
		const walk = (cursor: string): void => {
			fetchMessagePage('unread', cursor, 50).then((page: MessagePage) => {
				total += page.items.length
				if (page.hasMore && page.cursor != '' && total < 5000) {
					walk(page.cursor)
				} else {
					resolve(total)
				}
			})
		}
		walk('')
	})
}

/** POST /messages/{id}/read：单条已读；返回是否成功 */
export function markMessageRead(msgId: number): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		post('/api/v1/messages/' + msgId + '/read', {}).then((res: UTSJSONObject | null) => {
			resolve(res != null)
		})
	})
}

/** POST /messages/read-all：全部已读；返回是否成功 */
export function markAllRead(): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		post('/api/v1/messages/read-all', {}).then((res: UTSJSONObject | null) => {
			resolve(res != null)
		})
	})
}
