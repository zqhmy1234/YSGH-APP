/**
 * 事件手动操作（S-MO-1 · B3-5 用户操作优先，算法永不覆盖）
 *
 * 后端已就绪（api/events.py：merge/split/confirm，test_event_ops 7 项全过）。
 * MVP UI 范围：confirm（转正）+ merge（合并到相邻上一张）；split 后置
 * （客户端 timeline 无事件内容列表，需后端补 GET /events/{id}/items 后做选片拆分）。
 *
 * 契约：
 *  - POST /api/v1/events/confirm {event_id, title?}  → EventOut（status=confirmed，算法不再改动）
 *  - POST /api/v1/events/merge {target_event_id, source_event_ids:[>=1]} → EventOut
 */
import { post, dataObj } from './api'

/** 确认事件（转正；title 可空=保持原标题）→ 成功 true */
export function confirmEvent(eventId: string): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		const body: UTSJSONObject = {
			event_id: eventId
		}
		post('/api/v1/events/confirm', body).then((resp: UTSJSONObject | null) => {
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
}

/** 合并：source 并入 target（source 软删，target 置 confirmed）→ 成功 true */
export function mergeEvent(sourceEventId: string, targetEventId: string): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		const sourceIds: Array<string> = [sourceEventId]
		const body: UTSJSONObject = {
			target_event_id: targetEventId,
			source_event_ids: sourceIds
		}
		post('/api/v1/events/merge', body).then((resp: UTSJSONObject | null) => {
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
}
