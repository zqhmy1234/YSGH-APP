/**
 * 客户端契约类型（O15 · R3 重构侦察：与 OpenAPI 对齐的单一契约源）
 *
 * 仓库存在 docs/openapi.json（46 个 path 完整 schema），此前客户端零引用、零联动——
 * 各模块以字符串字面量手工拼端点路径与字段名，跨文件漂移（改一处漏一处）。
 * 本模块收口：
 *  - 端点路径常量（PATH_*，与 openapi.json paths 逐字对齐）
 *  - 关键字段名常量（FIELD_*，与 DTO schema 属性名逐字对齐）
 *  - 分页信封解析（Page{items, cursor, has_more}，openapi 定标 R4#6 后客户端对齐）
 *
 * 消费方（git grep 契约）：play（messages 分页）、upload_protocol（上传路径/字段）、
 * search_api（搜索路径）、event_sync（事件上云路径）、voice（asr/内容字段）、
 * sync_client（sync 三端点）。新增端点/字段改本文件一处即可。
 */

// ═══════════ 端点路径（对齐 docs/openapi.json paths）═══════════

export const PATH_AUTH_WECHAT: string = '/api/v1/auth/wechat'
export const PATH_AUTH_REFRESH: string = '/api/v1/auth/refresh'
export const PATH_AUTH_LOGOUT: string = '/api/v1/auth/logout'
export const PATH_CONTENTS: string = '/api/v1/contents'
export const PATH_SEARCH: string = '/api/v1/search'
export const PATH_SEARCH_IMAGE: string = '/api/v1/search/image'
export const PATH_EVENTS_SYNC: string = '/api/v1/events/sync'
export const PATH_EVENTS_TIMELINE: string = '/api/v1/events/timeline'
export const PATH_EVENTS_CONFIRM: string = '/api/v1/events/confirm'
export const PATH_EVENTS_MERGE: string = '/api/v1/events/merge'
export const PATH_EVENTS_SPLIT: string = '/api/v1/events/split'
export const PATH_ASR_TRANSCRIBE: string = '/api/v1/asr/transcribe'
export const PATH_CLASSIFY: string = '/api/v1/classify'
export const PATH_CORRECTIONS: string = '/api/v1/corrections'
export const PATH_MESSAGES: string = '/api/v1/messages'
export const PATH_SYNC_PUSH: string = '/api/v1/sync/push'
export const PATH_SYNC_PULL: string = '/api/v1/sync/pull'
export const PATH_SYNC_RECONCILE: string = '/api/v1/sync/reconcile'
export const PATH_UPLOAD_INIT: string = '/api/v1/upload/init'
export const PATH_UPLOAD_CHUNK: string = '/api/v1/upload/chunk'
export const PATH_UPLOAD_COMPLETE: string = '/api/v1/upload/complete'
export const PATH_UPLOAD_STATUS: string = '/api/v1/upload/status'
export const PATH_ECHO_TODAY: string = '/api/v1/echo/today'
export const PATH_INTERVIEW_QUESTIONS: string = '/api/v1/interview/questions'
export const PATH_INTERVIEW_ANSWERS: string = '/api/v1/interview/answers'
export const PATH_INTERVIEW_PROFILE: string = '/api/v1/interview/profile'

// ═══════════ 关键字段名（对齐 openapi.json DTO schema 属性名）═══════════

/** 上传协议字段 */
export const FIELD_UPLOAD_ID: string = 'upload_id'
export const FIELD_CLIENT_UPLOAD_ID: string = 'client_upload_id'
export const FIELD_CHUNK_INDEX: string = 'chunk_index'
export const FIELD_FILE_NAME: string = 'file_name'
export const FIELD_FILE_SIZE: string = 'file_size'
export const FIELD_UPLOAD_MODE: string = 'upload_mode'
export const FIELD_FILE_KEY: string = 'file_key'
export const FIELD_MISSING_CHUNKS: string = 'missing_chunks'
/** 内容/事件字段 */
export const FIELD_CONTENT_ID: string = 'content_id'
export const FIELD_CONTENT_TYPE: string = 'content_type'
export const FIELD_TAKEN_AT: string = 'taken_at'
export const FIELD_SOURCE: string = 'source'
export const FIELD_CLIENT_EVENT_ID: string = 'client_event_id'
export const FIELD_EVENT_ID: string = 'event_id'
export const FIELD_STATUS: string = 'status'
/** 信封字段 */
export const FIELD_CODE: string = 'code'
export const FIELD_MESSAGE: string = 'message'
export const FIELD_DATA: string = 'data'
/** 分页字段（openapi Page{items, cursor, has_more}，R4#6 定标） */
export const FIELD_ITEMS: string = 'items'
export const FIELD_CURSOR: string = 'cursor'
export const FIELD_HAS_MORE: string = 'has_more'

// ═══════════ 分页信封解析（O17：客户端与后端 cursor 分页字段对齐）═══════════

/** 分页数据（openapi Page 信封的 data 段：items + cursor + has_more） */
export class PageData {
	items: Array<UTSJSONObject>
	/** 下一页游标（无更多时为空串） */
	cursor: string
	hasMore: boolean

	constructor(items: Array<UTSJSONObject>, cursor: string, hasMore: boolean) {
		this.items = items
		this.cursor = cursor
		this.hasMore = hasMore
	}
}

/** 解析分页信封 data 段 → PageData；无 items 返回 null（调用方按空列表处理） */
export function parsePageData(d: UTSJSONObject): PageData | null {
	const items = d.getArray(FIELD_ITEMS)
	if (items == null) {
		return null
	}
	const cursorRaw = d.getString(FIELD_CURSOR)
	const cursor = cursorRaw != null ? cursorRaw : ''
	const hasMore = d.getBoolean(FIELD_HAS_MORE) ?? false
	return new PageData(items as Array<UTSJSONObject>, cursor, hasMore)
}
