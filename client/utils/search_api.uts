/**
 * 搜索入口（T-SR-1/2/3/4）：描述性搜索 + 以图搜图 + 降级态
 *
 * 链路（后端契约）：
 *  POST /api/v1/search            → {q, intent, content_types?, time_from?, time_to?, place?, tag?, limit}
 *    → SearchResult {query, rewritten_query, intent, hits[], total, latency_ms, degraded}
 *  POST /api/v1/search/image      → multipart file（jpg/png ≤10MB）+ limit → SearchResult
 *  SearchHit {content_id, content_type, text, taken_at, place, event_id, event_title, score, trace{}}
 *
 * 降级态（T-SR-4）：degraded=true（Qdrant 降级纯 PG 检索）→ 页面黄条提示；
 * 网络失败/后端 5xx → 错误态（resolve(null) + toast）。
 *
 * 约定：resolve-only（永不 reject），失败 resolve(null) + toast。
 */
// O9 收口：错误信封解析统一走 api.ts 的 parseErrorString（原本地 parseSearchError 副本已删）
// R1#17：multipart 上传统一走 api.ts uploadFileHttp（401 重放 + 5xx Sentry + 信封解析）
// O15：搜索端点路径统一走 contract.ts（与 OpenAPI 对齐）
import { post, dataObj, showErrorToast, uploadFileHttp, HttpResult } from './api'
import { PATH_SEARCH, PATH_SEARCH_IMAGE } from './contract'

export class SearchHitItem {
	contentId: string
	contentType: string
	text: string
	takenAt: string
	place: string
	eventId: string
	eventTitle: string
	score: number
	trace: string // 溯源摘要（trace dict → 可读文本）

	constructor(
		contentId: string,
		contentType: string,
		text: string,
		takenAt: string,
		place: string,
		eventId: string,
		eventTitle: string,
		score: number,
		trace: string
	) {
		this.contentId = contentId
		this.contentType = contentType
		this.text = text
		this.takenAt = takenAt
		this.place = place
		this.eventId = eventId
		this.eventTitle = eventTitle
		this.score = score
		this.trace = trace
	}
}

export class SearchOutcome {
	query: string
	rewrittenQuery: string
	intent: string
	hits: Array<SearchHitItem>
	total: number
	latencyMs: number
	degraded: boolean

	constructor(
		query: string,
		rewrittenQuery: string,
		intent: string,
		hits: Array<SearchHitItem>,
		total: number,
		latencyMs: number,
		degraded: boolean
	) {
		this.query = query
		this.rewrittenQuery = rewrittenQuery
		this.intent = intent
		this.hits = hits
		this.total = total
		this.latencyMs = latencyMs
		this.degraded = degraded
	}
}

/** 内容类型中文名（结果卡片徽标） */
export function contentTypeCn(t: string): string {
	if (t == 'photo') {
		return '照片'
	}
	if (t == 'voice') {
		return '语音'
	}
	if (t == 'article') {
		return '文章'
	}
	return '文字'
}

/** 溯源 dict → 可读摘要（后端 trace 结构：{matched, dense_score, sparse_score, rrf}） */
function traceToText(trace: UTSJSONObject | null): string {
	if (trace == null) {
		return ''
	}
	const parts: Array<string> = []
	// matched 召回通道
	const matched = trace.getArray('matched')
	if (matched != null && matched.length > 0) {
		const names: Array<string> = []
		for (let i = 0; i < matched.length; i++) {
			const m = matched[i] as string
			if (m == 'dense') {
				names.push('语义')
			} else if (m == 'sparse') {
				names.push('关键词')
			} else {
				names.push(m)
			}
		}
		parts.push('召回：' + names.join('+'))
	}
	// 分数
	const dense = trace.getNumber('dense_score')
	const sparse = trace.getNumber('sparse_score')
	if (dense != null && dense > 0) {
		parts.push('语义分 ' + Math.round(dense * 1000) / 10)
	}
	if (sparse != null && sparse > 0) {
		parts.push('关键词分 ' + Math.round(sparse * 1000) / 10)
	}
	return parts.join(' · ')
}

/** 解析 SearchResult 信封 → SearchOutcome；失败 resolve(null) */
function parseSearch(body: UTSJSONObject | null): SearchOutcome | null {
	if (body == null) {
		return null
	}
	const d = dataObj(body)
	if (d == null) {
		return null
	}
	const hits: Array<SearchHitItem> = []
	const arr = d.getArray('hits')
	if (arr != null) {
		for (let i = 0; i < arr.length; i++) {
			const h = arr[i] as UTSJSONObject
			hits.push(
				new SearchHitItem(
					h.getString('content_id') ?? '',
					h.getString('content_type') ?? 'text',
					h.getString('text') ?? '',
					h.getString('taken_at') ?? '',
					h.getString('place') ?? '',
					h.getString('event_id') ?? '',
					h.getString('event_title') ?? '',
					h.getNumber('score') as number,
					traceToText(h.getJSON('trace'))
				)
			)
		}
	}
	return new SearchOutcome(
		d.getString('query') ?? '',
		d.getString('rewritten_query') ?? '',
		d.getString('intent') ?? '',
		hits,
		d.getNumber('total') as number,
		d.getNumber('latency_ms') as number,
		d.getBoolean('degraded') ?? false
	)
}

/** POST /search：描述性搜索；contentTypes 非空时按 photo/voice/text/article 过滤；返回 SearchOutcome 或 null */
export function searchText(query: string, limit: number, contentTypes: Array<string>): Promise<SearchOutcome | null> {
	const body: UTSJSONObject = {
		q: query,
		limit: limit
	}
	// 仅在指定类型时下发 content_types（空数组=全部，不下发避免后端误判）
	if (contentTypes.length > 0) {
		body.set('content_types', contentTypes)
	}
	return new Promise<SearchOutcome | null>((resolve) => {
		post(PATH_SEARCH, body).then((res: UTSJSONObject | null) => {
			resolve(parseSearch(res))
		})
	})
}

/** POST /search/image：以图搜图（multipart）；返回 SearchOutcome 或 null
 *  R1#17：multipart 上传统一走 api.ts uploadFileHttp（401 重放 + 5xx Sentry + 信封解析） */
export function searchByImage(filePath: string, limit: number): Promise<SearchOutcome | null> {
	return new Promise<SearchOutcome | null>((resolve) => {
		uploadFileHttp(PATH_SEARCH_IMAGE + '?limit=' + limit, filePath, 'file', null, 30000).then((hr: HttpResult) => {
			if (hr.status === 200) {
				// uploadFile res.data 是 string（lessons.md #3）；uploadFileHttp 已按信封解析
				resolve(parseSearch(hr.body))
				return
			}
			const msg = hr.body != null ? (hr.body.getString('message') ?? '搜索失败（HTTP ' + hr.status + '）') : '搜索失败（HTTP ' + hr.status + '）'
			showErrorToast(new Error(msg))
			resolve(null)
		})
	})
}
