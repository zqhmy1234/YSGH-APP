/**
 * 时间工具纯函数（TD-P2B · S1-M4：ISO8601 手工拼装 4 份收口）
 *
 * 此前 uploader.isoString / event_ops.isoNow / sync_client.isoNow（+08:00 本地，逐字
 * 一致）与 sentry.isoNow（UTC 毫秒变体）各写一份；显示层日期（timeline.dayKey/
 * friendlyDay、search.shortDate）各自为政。UTS Date 无 toISOString/toLocaleString，
 * 本模块集中实现，零依赖纯函数——event_ops/sync_client 的"本地一份避免循环依赖"
 * 从此不再需要。
 *
 * 注：isoLocal 沿用既有硬编码 +08:00 后缀语义（真机时区非 +08 的偏差是历史行为，
 * 收敛后单点可改）；sentry 用 UTC 变体 isoUtc。
 */
function pad2(n: number): string {
	return n < 10 ? '0' + n : '' + n
}

function pad3(n: number): string {
	if (n < 10) {
		return '00' + n
	}
	if (n < 100) {
		return '0' + n
	}
	return '' + n
}

/** epoch ms → ISO8601 本地时间（+08:00 后缀；同原 uploader.isoString / sync.isoNow） */
export function isoLocal(ms: number): string {
	const d = new Date(ms)
	return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) +
		'T' + pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds()) + '+08:00'
}

/** epoch ms → ISO8601 UTC（毫秒 + Z；同原 sentry.isoNow，Sentry Envelope sent_at 用） */
export function isoUtc(ms: number): string {
	const d = new Date(ms)
	return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate()) +
		'T' + pad2(d.getUTCHours()) + ':' + pad2(d.getUTCMinutes()) + ':' + pad2(d.getUTCSeconds()) +
		'.' + pad3(d.getUTCMilliseconds()) + 'Z'
}

/** 日期分组键：epoch ms → yyyy-mm-dd（本地时区；同原 timeline.dayKey） */
export function dayKey(ms: number): string {
	const d = new Date(ms)
	return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate())
}

/** 友好日期标题：8月24日 · 周一（本地时区；同原 timeline.friendlyDay） */
export function friendlyDay(ms: number): string {
	const d = new Date(ms)
	const weeks: Array<string> = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
	return (d.getMonth() + 1) + '月' + d.getDate() + '日 · ' + weeks[d.getDay()]
}

/** ISO 串 → 短日期（yyyy-mm-dd → mm月dd日；同原 search.uvue shortDate） */
export function shortDate(iso: string): string {
	const tIdx = iso.indexOf('T')
	const datePart = tIdx >= 0 ? iso.substring(0, tIdx) : iso
	const parts = datePart.split('-')
	if (parts.length !== 3) {
		return datePart
	}
	return parseInt(parts[1]) + '月' + parseInt(parts[2]) + '日'
}
