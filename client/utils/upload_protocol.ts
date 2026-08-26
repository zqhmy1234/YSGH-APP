/**
 * 上传分片协议共享层（TD-P2B · S1-H3：uploader/voice 整段复制收口）
 *
 * uploader.ts 与 voice.ts 此前各写一份 urlEncode/formPost/fieldOf + init/chunk/complete
 * 请求（仅类名 HttpResp vs VoiceHttpResp、个别日志不同）——上传协议两份漂移源，
 * 改一处漏一处（如 URL 编码加字符需同步两处）。现统一本模块：
 *  - urlEncode / formPost（返回 {status, raw}）/ fieldOf 底层工具
 *  - initUpload / putChunk / completeUpload 三个协议请求
 * 调用方各自保留业务编排（uploader 断点续传队列 / voice 录音状态机）。
 * 错误语义：协议层只透传 status（0=网络失败），4xx 停条 / 5xx 重试由调用方按业务决定。
 */
import { getBaseUrl } from './config'
import { getToken } from './auth'

/** 表单响应：status（0=网络失败） + raw（响应 JSON 字符串） */
export class UploadResp {
	status: number
	raw: string

	constructor(status: number, raw: string) {
		this.status = status
		this.raw = raw
	}
}

/** URL 编码最小集（路径/file_name/upload_id/meta 均为 ASCII 可控字符；中文文件名后续补全） */
export function urlEncode(s: string): string {
	let out = ''
	for (let i = 0; i < s.length; i++) {
		const c = s.charAt(i)
		if (c == '&') {
			out += '%26'
		} else if (c == '=') {
			out += '%3D'
		} else if (c == '%') {
			out += '%25'
		} else if (c == '+') {
			out += '%2B'
		} else if (c == ' ') {
			out += '%20'
		} else {
			out += c
		}
	}
	return out
}

/** POST 表单（application/x-www-form-urlencoded；后端 Form 字段）→ UploadResp */
export function formPost(path: string, body: string): Promise<UploadResp> {
	return new Promise<UploadResp>((resolve) => {
		const header: UTSJSONObject = {
			'Content-Type': 'application/x-www-form-urlencoded'
		}
		const token = getToken()
		if (token != '') {
			header.set('Authorization', 'Bearer ' + token)
		}
		uni.request({
			url: getBaseUrl() + path,
			method: 'POST',
			data: body,
			header: header,
			timeout: 30000,
			success: (res) => {
				if (res.statusCode === 200) {
					resolve(new UploadResp(200, JSON.stringify(res.data)))
				} else {
					console.error('[yishu] upload form ' + res.statusCode + ' ' + path)
					resolve(new UploadResp(res.statusCode, JSON.stringify(res.data)))
				}
			},
			fail: () => {
				console.error('[yishu] upload form NETWORK ' + path)
				resolve(new UploadResp(0, ''))
			}
		})
	})
}

/** 取响应 JSON 字符串里某字段值（"key":"value" 或 "key":value；value 不含引号时原样返回） */
export function fieldOf(raw: string, key: string): string {
	const needle = '"' + key + '":'
	const idx = raw.indexOf(needle)
	if (idx < 0) {
		return ''
	}
	const rest = raw.substring(idx + needle.length)
	if (rest.startsWith('"')) {
		return rest.substring(1).split('"')[0]
	}
	const end = rest.indexOf(',')
	return end >= 0 ? rest.substring(0, end) : rest
}

/** init：建上传任务（client_upload_id 幂等；单块协议 chunk_size=file_size，upload_mode 透传）→ UploadResp */
export function initUpload(clientUploadId: string, fileName: string, fileSize: number, uploadMode: string): Promise<UploadResp> {
	const body = 'client_upload_id=' + urlEncode(clientUploadId) +
		'&file_name=' + urlEncode(fileName) +
		'&file_size=' + fileSize +
		'&chunk_size=' + fileSize +
		'&upload_mode=' + uploadMode
	return formPost('/api/v1/upload/init', body)
}

/** chunk：传单片（POST multipart；后端幂等 + 大小校验）→ HTTP status（0=网络失败） */
export function putChunk(uploadId: string, filePath: string, timeout: number): Promise<number> {
	return new Promise<number>((resolve) => {
		const form: UTSJSONObject = {
			upload_id: uploadId,
			chunk_index: '0'
		}
		uni.uploadFile({
			url: getBaseUrl() + '/api/v1/upload/chunk',
			filePath: filePath,
			name: 'file',
			formData: form,
			header: { 'Authorization': 'Bearer ' + getToken() },
			timeout: timeout,
			success: (res) => {
				if (res.statusCode === 200) {
					resolve(200)
				} else {
					console.error('[yishu] chunk HTTP ' + res.statusCode)
					resolve(res.statusCode)
				}
			},
			fail: () => {
				console.error('[yishu] chunk NETWORK')
				resolve(0)
			}
		})
	})
}

/** complete：完成 + 建内容记录（meta 与 /contents/upload 对齐）→ UploadResp（调用方取 content_id/file_key） */
export function completeUpload(uploadId: string, meta: UTSJSONObject): Promise<UploadResp> {
	const body = 'upload_id=' + urlEncode(uploadId) + '&meta=' + urlEncode(JSON.stringify(meta))
	return formPost('/api/v1/upload/complete', body)
}
