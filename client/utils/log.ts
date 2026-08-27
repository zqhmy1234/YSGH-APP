/**
 * 客户端日志脱敏（O19 · R3 重构侦察：token/手机号/敏感字段不落日志）
 *
 * 此前 text_recorder 轮询把全量响应（含用户 text）刷日志、event_sync 4xx 把
 * 响应体（含用户事件内容）刷日志——敏感信息落 logcat。本模块提供统一脱敏：
 *  - 掩码 Authorization/Bearer token（'Bearer xxx' → 'Bearer ***'）
 *  - 掩码 11 位大陆手机号（1[3-9] 开头）
 *  - 掩码 JSON 中敏感键的值（token/access_token/refresh_token/phone/password/code/sms_code）
 *  - 掩码超长文本段（用户 text 等 → 截断保留前 40 字符，防整段内容落日志）
 * 纪律：任何可能含用户内容/凭据的日志必须先经 redactLog() 再输出。
 */
export const REDACTED: string = '***'

/** 掩码 11 位大陆手机号（1[3-9]xxxxxxxxx）→ 1********** */
function maskPhone(s: string): string {
	let out = ''
	let i = 0
	while (i < s.length) {
		const c = s.charAt(i)
		if (c >= '0' && c <= '9') {
			// 尝试匹配 11 位连续数字且首位 1、次位 3-9
			if (c == '1' && i + 1 < s.length) {
				const c2 = s.charAt(i + 1)
				if (c2 >= '3' && c2 <= '9') {
					let j = i + 2
					let allDigit = true
					while (j < s.length && j < i + 11) {
						const cj = s.charAt(j)
						if (!(cj >= '0' && cj <= '9')) {
							allDigit = false
							break
						}
						j++
					}
					if (allDigit && j == i + 11) {
						out += '1**********'
						i = j
						continue
					}
				}
			}
			out += c
			i++
		} else {
			out += c
			i++
		}
	}
	return out
}

/** 掩码 Bearer token（'Bearer xxxxx' → 'Bearer ***'；大小写不敏感） */
function maskBearer(s: string): string {
	const lower = s.toLowerCase()
	const idx = lower.indexOf('bearer ')
	if (idx < 0) {
		return s
	}
	const start = idx + 'bearer '.length
	let end = start
	while (end < s.length) {
		const c = s.charAt(end)
		if (c == ' ' || c == '\n' || c == '"' || c == ',' || c == '}') {
			break
		}
		end++
	}
	return s.substring(0, start) + REDACTED + s.substring(end)
}

/** 掩码 JSON 敏感键的值（"key":"value" 或 "key":value → "***"；值超长截断） */
function maskSensitiveJsonKeys(raw: string): string {
	const keys: Array<string> = [
		'token', 'access_token', 'refresh_token', 'phone', 'password', 'code', 'sms_code', 'secret'
	]
	let out = raw
	for (let k = 0; k < keys.length; k++) {
		out = maskJsonKey(out, keys[k])
	}
	return out
}

function maskJsonKey(raw: string, key: string): string {
	const needle = '"' + key + '":'
	let out = raw
	let idx = out.indexOf(needle)
	while (idx >= 0) {
		const rest = out.substring(idx + needle.length)
		const head = out.substring(0, idx + needle.length)
		if (rest.startsWith('"')) {
			// 字符串值："..."（含转义——简单处理到下一个未转义引号）
			let end = 1
			while (end < rest.length) {
				const c = rest.charAt(end)
				if (c == '\\') {
					end += 2
					continue
				}
				if (c == '"') {
					break
				}
				end++
			}
			out = head + '"' + REDACTED + '"' + rest.substring(end + 1)
		} else {
			// 非字符串值：截到逗号/右括号
			let end = 0
			while (end < rest.length) {
				const c = rest.charAt(end)
				if (c == ',' || c == '}' || c == ']' || c == '\n') {
					break
				}
				end++
			}
			out = head + REDACTED + rest.substring(end)
		}
		idx = out.indexOf(needle, idx + needle.length)
	}
	return out
}

/** 截断超长连续段（防用户长文本整段落日志）：>120 字符的连续非空白段保留前 60 + 省略号 */
function truncateLong(s: string): string {
	let out = ''
	let runStart = 0
	for (let i = 0; i < s.length; i++) {
		const c = s.charAt(i)
		if (c == ' ' || c == '\n' || c == '\t' || c == ',' || c == '}') {
			const len = i - runStart
			if (len > 120) {
				out += s.substring(runStart, runStart + 60) + '…[+截断]'
			} else {
				out += s.substring(runStart, i)
			}
			out += c
			runStart = i + 1
		}
	}
	if (runStart < s.length) {
		const len = s.length - runStart
		if (len > 120) {
			out += s.substring(runStart, runStart + 60) + '…[+截断]'
		} else {
			out += s.substring(runStart)
		}
	}
	return out
}

/** 日志脱敏总入口：Bearer → 手机号 → JSON 敏感键 → 长段截断（顺序执行） */
export function redactLog(raw: string): string {
	if (raw == '') {
		return raw
	}
	let out = raw
	out = maskBearer(out)
	out = maskPhone(out)
	out = maskSensitiveJsonKeys(out)
	out = truncateLong(out)
	return out
}
