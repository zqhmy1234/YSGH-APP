/**
 * 事件手动操作（S-MO-1 · B3-5 用户操作优先，算法永不覆盖）
 *
 * 后端已就绪（api/events.py：merge/split/confirm + GET /events/{id}/items，
 * test_event_ops 9 项全过）。MVP UI 范围：confirm（转正）+ merge（合并到相邻
 * 上一张）+ split（选片拆分，2026-08-25 后端补 items 端点后启用）。
 *
 * 2026-08-26 Wave2 AgentE 新增：
 *  - fetchContentEvents：照片→事件反向入口（GET /api/v1/contents/{id}/events，
 *    新文件 api/event_items.py 提供；照片详情"属于"列表）
 *  - ignoreEventLocally/isIgnoredEvent：L2 待确认区"忽略"——MVP 本地忽略
 *    （后端无 reject 端点，Agent D 域；本地隐藏 + 可被未来 reject API 取代）
 *
 * 离线 op_log（2026-08-25 · 第二波遗留）：网络不可用时操作不丢失——
 * 入本地队列（uni storage，JSON 行分隔串），联网后 flushOpQueue 按序补发。
 * 后端 merge/split/confirm 非幂等（无 op_id），队列顺序执行 + 网络恢复后
 * 单飞 flush，重复概率极低；XView（SQLCipher op_log 表）随自定义基座落地。
 *
 * 契约：
 *  - GET  /api/v1/events/{id}/items           → 成员明细（split 选片）
 *  - POST /api/v1/events/confirm {event_id}   → EventOut（status=confirmed）
 *  - POST /api/v1/events/merge {target_event_id, source_event_ids:[>=1]}
 *  - POST /api/v1/events/split {event_id, content_ids:[>=1]}
 *  - GET  /api/v1/contents/{id}/events        → 照片所属事件列表（反向入口）
 */
import { post, get, dataObj, dataArr } from './api'
import { isoLocal, parseIsoMs } from './time'
import { getNetKind, NetKind } from './uploader'
// O6/F9：队列存储单源 queue_store（与 sync_client 双离线队列合并单 key，路由差异保留）；
// event flush 退避统一走 retry.ts retryAsync（2s→4s→8s→8s→8s，5 次上限）
import { enqueueEntry, countPendingOfTypes, allPendingOfTypes, removeByIds, bumpRetryById } from './queue_store'
import { retryAsync } from './retry'

const IGNORE_KEY = 'yishu_ignored_events'

/** 事件类型操作（与 sync_client upsert_field/delete 区分，同队列按 op_type 路由） */
const EVENT_TYPES: Array<string> = ['confirm', 'merge', 'split']

/** 拆分选片条目（GET /events/{id}/items 解析） */
export class SplitItem {
	contentId: string
	contentType: string
	title: string
	takenAt: number
	selected: boolean

	constructor(contentId: string, contentType: string, title: string, takenAt: number) {
		this.contentId = contentId
		this.contentType = contentType
		this.title = title
		this.takenAt = takenAt
		this.selected = false
	}
}

/** 照片所属事件（反向入口 GET /contents/{id}/events 解析；照片详情"属于"列表） */
export class ContentEventRef {
	id: string
	level: number
	title: string
	coverContentId: string
	status: string
	confidence: number
	photoCount: number

	constructor(id: string, level: number, title: string, coverContentId: string, status: string, confidence: number, photoCount: number) {
		this.id = id
		this.level = level
		this.title = title
		this.coverContentId = coverContentId
		this.status = status
		this.confidence = confidence
		this.photoCount = photoCount
	}
}

/** 照片→事件反向查询（B3-4：照片详情"属于：事件列表"）；失败/无归属 → 空数组 */
export function fetchContentEvents(contentId: string): Promise<Array<ContentEventRef>> {
	return new Promise<Array<ContentEventRef>>((resolve) => {
		get('/api/v1/contents/' + contentId + '/events').then((resp: UTSJSONObject | null) => {
			if (resp == null) {
				resolve([])
				return
			}
			const arr = dataArr(resp)
			const out: Array<ContentEventRef> = []
			for (let i = 0; i < arr.length; i++) {
				const it = arr[i]
				out.push(new ContentEventRef(
					it.getString('id') ?? '',
					it.getNumber('level') as number,
					it.getString('title') ?? '',
					it.getString('cover_content_id') ?? '',
					it.getString('status') ?? 'draft',
					it.getNumber('confidence') as number,
					it.getNumber('photo_count') as number
				))
			}
			resolve(out)
		})
	})
}

	/** 单条内容详情（记忆详情页；GET /contents?content_id= 精确取，失败 null） */
	export class ContentDetail {
		id: string
		contentType: string
		text: string
		takenAt: string
		place: string
		status: string

		constructor(id: string, contentType: string, text: string, takenAt: string, place: string, status: string) {
			this.id = id
			this.contentType = contentType
			this.text = text
			this.takenAt = takenAt
			this.place = place
			this.status = status
		}
	}

	export function fetchContentDetail(contentId: string): Promise<ContentDetail | null> {
		return new Promise<ContentDetail | null>((resolve) => {
			get('/api/v1/contents?content_id=' + contentId + '&limit=1').then((resp: UTSJSONObject | null) => {
				if (resp == null) {
					resolve(null)
					return
				}
				const d = dataObj(resp)
				if (d == null) {
					resolve(null)
					return
				}
				const arr = d.getArray('items')
				if (arr == null || arr.length == 0) {
					resolve(null)
					return
				}
				const it = arr[0] as UTSJSONObject
				resolve(
					new ContentDetail(
						it.getString('id') ?? '',
						it.getString('content_type') ?? '',
						it.getString('text') ?? '',
						it.getString('taken_at') ?? '',
						it.getString('place') ?? '',
						it.getString('status') ?? ''
					)
				)
			})
		})
	}

/** L2 待确认区"忽略"（MVP 本地隐藏；后端 reject 端点由 Agent D 域，后续可替换） */
export function ignoreEventLocally(eventId: string): void {
	const raw = uni.getStorageSync(IGNORE_KEY) as string
	let ids: Array<string> = []
	if (raw != '') {
		ids = raw.split('\n')
	}
	for (let i = 0; i < ids.length; i++) {
		if (ids[i] == eventId) {
			return
		}
	}
	ids.push(eventId)
	uni.setStorageSync(IGNORE_KEY, ids.join('\n'))
}

/** 事件是否被本地忽略（待确认区过滤） */
export function isIgnoredEvent(eventId: string): boolean {
	const raw = uni.getStorageSync(IGNORE_KEY) as string
	if (raw == '') {
		return false
	}
	const ids = raw.split('\n')
	for (let i = 0; i < ids.length; i++) {
		if (ids[i] == eventId) {
			return true
		}
	}
	return false
}

/** 事件成员照片映射项：event_id → content_id[]（端侧聚合成员；F10 余项从 index.uvue 迁入） */
export class EventPhotoEntry {
	eventId: string
	contentIds: Array<string>

	constructor(eventId: string, contentIds: Array<string>) {
		this.eventId = eventId
		this.contentIds = contentIds
	}
}

/** 通过 sync accepted 明细建立 服务端事件 id → 成员照片 映射（端侧聚合的 client_event_id 与云端 id 不同；
 *  F10 余项：从 index.uvue buildEventPhotoIds 迁入的纯函数，行为等价；返回新映射，不直接改调用方状态） */
export function mapAcceptedToClientIds(clientEvents: Array<UTSJSONObject>, accepted: Array<UTSJSONObject>): Array<EventPhotoEntry> {
	const clientMap: Array<EventPhotoEntry> = []
	for (let k = 0; k < clientEvents.length; k++) {
		const cid = clientEvents[k].getString('client_event_id') ?? ''
		const pids = clientEvents[k].getArray('photo_ids') as Array<string> | null
		if (cid != '' && pids != null) {
			clientMap.push(new EventPhotoEntry(cid, pids))
		}
	}
	const out: Array<EventPhotoEntry> = []
	for (let a = 0; a < accepted.length; a++) {
		const it = accepted[a]
		const serverId = it.getString('event_id') ?? ''
		const clientId = it.getString('client_event_id') ?? ''
		if (serverId == '' || clientId == '') {
			continue
		}
		for (let k = 0; k < clientMap.length; k++) {
			if (clientMap[k].eventId == clientId) {
				out.push(new EventPhotoEntry(serverId, clientMap[k].contentIds))
				break
			}
		}
	}
	return out
}

/** 确认事件（转正；title 可空=保持原标题）→ 成功 true（离线则入队返回 true） */
export function confirmEvent(eventId: string): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		getNetKind((kind: NetKind) => {
			const online = kind != 'none'
			if (!online) {
				const body: UTSJSONObject = {
					event_id: eventId
				}
				enqueueOp('confirm', body)
				resolve(true)
				return
			}
			const body2: UTSJSONObject = {
				event_id: eventId
			}
			post('/api/v1/events/confirm', body2).then((resp: UTSJSONObject | null) => {
				if (resp == null) {
					resolve(false)
					return
				}
				const data = dataObj(resp)
				if (data == null) {
					resolve(false)
					return
				}
				resolve(data.getString('id') != null)
			})
		})
	})
}

/** 合并：source 并入 target（source 软删，target 置 confirmed）→ 成功 true */
export function mergeEvent(sourceEventId: string, targetEventId: string): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		getNetKind((kind: NetKind) => {
			const online = kind != 'none'
			if (!online) {
				const body: UTSJSONObject = {
					target_event_id: targetEventId,
					source_event_ids: [sourceEventId]
				}
				enqueueOp('merge', body)
				resolve(true)
				return
			}
			const sourceIds: Array<string> = [sourceEventId]
			const body2: UTSJSONObject = {
				target_event_id: targetEventId,
				source_event_ids: sourceIds
			}
			post('/api/v1/events/merge', body2).then((resp: UTSJSONObject | null) => {
				if (resp == null) {
					resolve(false)
					return
				}
				const data = dataObj(resp)
				if (data == null) {
					resolve(false)
					return
				}
				resolve(data.getString('id') != null)
			})
		})
	})
}

/** 拉取事件成员明细（split 选片）→ 空数组=失败/无成员 */
export function fetchEventItems(eventId: string): Promise<Array<SplitItem>> {
	return new Promise<Array<SplitItem>>((resolve) => {
		get('/api/v1/events/' + eventId + '/items').then((resp: UTSJSONObject | null) => {
			if (resp == null) {
				resolve([])
				return
			}
			const arr = dataArr(resp)
			const out: Array<SplitItem> = []
			for (let i = 0; i < arr.length; i++) {
				const it = arr[i]
				out.push(new SplitItem(
					it.getString('content_id') ?? '',
					it.getString('content_type') ?? 'text',
					it.getString('title') ?? '',
					parseIsoMs(it.getString('taken_at') ?? '')
				))
			}
			resolve(out)
		})
	})
}

/** 拆分：选中内容拆出建新事件 → 成功 true（离线则入队返回 true） */
export function splitEvent(eventId: string, contentIds: Array<string>): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		getNetKind((kind: NetKind) => {
			const online = kind != 'none'
			if (!online) {
				const body: UTSJSONObject = {
					event_id: eventId,
					content_ids: contentIds
				}
				enqueueOp('split', body)
				resolve(true)
				return
			}
			const body2: UTSJSONObject = {
				event_id: eventId,
				content_ids: contentIds
			}
			post('/api/v1/events/split', body2).then((resp: UTSJSONObject | null) => {
				if (resp == null) {
					resolve(false)
					return
				}
				const data = dataObj(resp)
				if (data == null) {
					resolve(false)
					return
				}
				resolve(data.getString('id') != null)
			})
		})
	})
}

/** 入离线队列（payload 为 UTSJSONObject；行分隔 JSON 串存储，存储共享 queue_store）
 *  2026-08-26 Wave3 H：队列补齐六字段契约（op_id/op_type/payload/status/created_at/retry_count），
 *  与 sync_client 后端 offline_queue 六字段对齐（audit_B4_sync §4）。 */
export function enqueueOp(opType: string, payload: UTSJSONObject): void {
	const entry: UTSJSONObject = {
		op_id: 'op_' + Date.now().toString(),
		op_type: opType,
		payload: payload,
		status: 'pending',
		created_at: isoNow(),
		retry_count: 0
	}
	enqueueEntry(entry)
	uni.showToast({ title: '已离线排队，联网后自动同步', icon: 'none' })
}

/** epoch ms → ISO8601 本地时间（S1-M4 收口：统一走 time.isoLocal，不再本地拼一份） */
function isoNow(): string {
	return isoLocal(Date.now())
}

/** 待同步队列条数（仅事件类型——共享队列按 op_type 过滤） */
export function pendingOpCount(): number {
	return countPendingOfTypes(EVENT_TYPES)
}

/** 联网后补发离线队列（按序；网络仍断则停，业务失败丢弃）→ 剩余条数 */
export function flushOpQueue(): Promise<number> {
	return new Promise<number>((resolve) => {
		const entries = allPendingOfTypes(EVENT_TYPES)
		if (entries.length === 0) {
			resolve(0)
			return
		}
		getNetKind((kind: NetKind) => {
			const online = kind != 'none'
			if (!online) {
				resolve(entries.length)
				return
			}
			flushNext(entries, 0, 0, resolve)
		})
	})
}

/** 单条带退避补发（O6/F9：flush 退避统一走 retry.ts retryAsync；失败可重试，
 *  退避耗尽后由调用方按网络探测区分 业务失败丢弃 / 离线保留）。
 *  O18：isFatal/onFail 恒 false（4xx 停批由 doOp 返回 false 实现），省略死参 */
function flushOne(opType: string, payload: UTSJSONObject): Promise<boolean> {
	return retryAsync<boolean>(
		() => doOp(opType, payload).then((ok: boolean): boolean | null => {
			return ok ? true : null
		})
	).then((r: boolean | null): boolean => {
		return r == true
	})
}

/** 单条处理（模块级函数：UTS 箭头函数不可自引用，递归必须用具名函数声明） */
function flushNext(entries: Array<UTSJSONObject>, idx: number, flushed: number, resolve: (n: number) => void): void {
	if (idx >= entries.length) {
		if (flushed > 0) {
			uni.showToast({ title: '已同步 ' + flushed + ' 条离线操作', icon: 'none' })
		}
		resolve(countPendingOfTypes(EVENT_TYPES))
		return
	}
	const e = entries[idx]
	const opType = e.getString('op_type') ?? ''
	const payload = e.getJSON('payload')
	if (payload == null) {
		removeByIds([e.getString('op_id') ?? '']) // 无 payload 视为脏数据丢弃
		flushNext(entries, idx + 1, flushed, resolve)
		return
	}
	flushOne(opType, payload).then((ok: boolean) => {
		if (ok) {
			removeByIds([e.getString('op_id') ?? ''])
			flushNext(entries, idx + 1, flushed + 1, resolve)
			return
		}
		// 退避耗尽仍失败：重新探测网络——仍断 → bumpRetry 保留 + 停（网络恢复再补发）；
		// 在线 → 业务失败丢弃（原语义）
		getNetKind((kind: NetKind) => {
			const online = kind != 'none'
			if (online) {
				removeByIds([e.getString('op_id') ?? ''])
				flushNext(entries, idx + 1, flushed, resolve)
			} else {
				bumpRetryById(e.getString('op_id') ?? '')
				resolve(countPendingOfTypes(EVENT_TYPES))
			}
		})
	})
}

/** 单条离线操作补发（按 op_type 路由到后端端点） */
function doOp(opType: string, payload: UTSJSONObject): Promise<boolean> {
	if (opType === 'confirm') {
		return confirmEvent(payload.getString('event_id') ?? '')
	} else if (opType === 'merge') {
		const targetId = payload.getString('target_event_id') ?? ''
		const sourceIds = payload.getArray('source_event_ids') as Array<string> | null
		if (targetId == '' || sourceIds == null || sourceIds.length == 0) {
			return Promise.resolve(false)
		}
		return mergeEvent(sourceIds[0], targetId)
	} else if (opType === 'split') {
		const eventId = payload.getString('event_id') ?? ''
		const ids = payload.getArray('content_ids') as Array<string> | null
		if (eventId == '' || ids == null || ids.length == 0) {
			return Promise.resolve(false)
		}
		return splitEvent(eventId, ids)
	}
	return Promise.resolve(false)
}
