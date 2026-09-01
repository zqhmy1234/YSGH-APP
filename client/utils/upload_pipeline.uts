/**
 * 单文件上传管线（O16 收口：uploader/voice 的 init→status→chunk→complete 编排统一封装）
 *
 * uploader.ts 与 voice.ts 此前各自写一遍 init→chunk→complete（含 4xx 停条 / 网络·5xx
 * 可重试的语义映射），两处漂移。现统一本模块：
 *  - 状态机：init（建任务，client_upload_id 幂等）→ status（断点查询缺失分片）→
 *    chunk（传单片）→ complete（建内容记录）→ {content_id, file_key}
 *  - 断点续传：调用方把已有 upload_id 传 resumeUploadId（应用重启后从持久化恢复），
 *    本模块跳过 init 直接 status 补传；新建时经 onNewUploadId 回调让调用方持久化 upload_id
 *  - 错误语义：UploadPipeError.permanent=true（4xx 停条，重试无意义）/ false（网络·5xx 可退避重试）
 * 调用方保留业务编排：uploader 批量并发池 + held/failed 队列 / voice 录音状态机。
 */
import { getBaseUrl } from './config'
import { getToken } from './auth'
import { initUpload, putChunk, completeUpload, fieldOf, UploadResp } from './upload_protocol'
import { PATH_UPLOAD_STATUS, FIELD_UPLOAD_ID, FIELD_MISSING_CHUNKS, FIELD_FILE_KEY, FIELD_CONTENT_ID } from './contract'

/** 单文件上传规格（调用方组装） */
export class UploadSpec {
	/** 幂等键（client_upload_id：照片 path / voice|path） */
	clientUploadId: string
	fileName: string
	fileSize: number
	filePath: string
	uploadMode: string
	/** 断点续传：已有 upload_id（'' = 新建任务） */
	resumeUploadId: string

	constructor(clientUploadId: string, fileName: string, fileSize: number, filePath: string, uploadMode: string, resumeUploadId: string = '') {
		this.clientUploadId = clientUploadId
		this.fileName = fileName
		this.fileSize = fileSize
		this.filePath = filePath
		this.uploadMode = uploadMode
		this.resumeUploadId = resumeUploadId
	}
}

/** 管线结果：content_id + file_key + 实际使用的 upload_id */
export class UploadOutcome {
	contentId: string
	fileKey: string
	uploadId: string

	constructor(contentId: string, fileKey: string, uploadId: string) {
		this.contentId = contentId
		this.fileKey = fileKey
		this.uploadId = uploadId
	}
}

/** 管线错误：permanent=true（4xx 停条，重试无意义）/ false（网络·5xx，可退避重试） */
export class UploadPipeError extends Error {
	status: number
	permanent: boolean

	constructor(status: number, message: string, permanent: boolean) {
		super(message)
		this.status = status
		this.permanent = permanent
	}
}

/** 建任务（client_upload_id 幂等）→ upload_id；4xx → permanent 错误 */
function initFor(spec: UploadSpec): Promise<string> {
	return new Promise<string>((resolve, reject) => {
		initUpload(spec.clientUploadId, spec.fileName, spec.fileSize, spec.uploadMode).then((resp: UploadResp) => {
			if (resp.status === 200) {
				const uploadId = fieldOf(resp.raw, FIELD_UPLOAD_ID)
				if (uploadId == '') {
					reject(new UploadPipeError(0, 'init 无 upload_id', false))
					return
				}
				resolve(uploadId)
				return
			}
			reject(new UploadPipeError(resp.status, 'init HTTP ' + resp.status, resp.status >= 400 && resp.status < 500))
		})
	})
}

/** 查断点状态：缺失分片 true（缺 0 或空数组=已完成）；非 200 视为需补传（交给 chunk 暴露真实错误） */
function statusMissing(uploadId: string): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		const header: UTSJSONObject = {}
		const token = getToken()
		if (token != '') {
			header.set('Authorization', 'Bearer ' + token)
		}
		uni.request({
			url: getBaseUrl() + PATH_UPLOAD_STATUS + '?upload_id=' + uploadId,
			method: 'GET',
			header: header,
			timeout: 15000,
			success: (res) => {
				if (res.statusCode === 200) {
					const raw = JSON.stringify(res.data)
					const missing = fieldOf(raw, FIELD_MISSING_CHUNKS)
					resolve(missing != '[]' && missing != '')
				} else {
					resolve(true)
				}
			},
			fail: () => resolve(true)
		})
	})
}

/** 传单片（multipart；后端幂等 + 大小校验）；4xx → permanent 错误 */
function putChunkFor(uploadId: string, filePath: string): Promise<boolean> {
	return new Promise<boolean>((resolve, reject) => {
		putChunk(uploadId, filePath, 60000).then((status: number) => {
			if (status === 200) {
				resolve(true)
				return
			}
			if (status >= 400 && status < 500) {
				reject(new UploadPipeError(status, 'chunk HTTP ' + status, true))
				return
			}
			reject(new UploadPipeError(status, 'chunk 网络失败', false))
		})
	})
}

/** complete：建内容记录（meta 调用方构造）→ content_id/file_key；4xx → permanent 错误 */
function completeFor(uploadId: string, meta: UTSJSONObject): Promise<UploadOutcome> {
	return new Promise<UploadOutcome>((resolve, reject) => {
		completeUpload(uploadId, meta).then((resp: UploadResp) => {
			if (resp.status === 200) {
				const cid = fieldOf(resp.raw, FIELD_CONTENT_ID)
				if (cid == '') {
					reject(new UploadPipeError(0, 'complete 无 content_id', false))
					return
				}
				resolve(new UploadOutcome(cid, fieldOf(resp.raw, FIELD_FILE_KEY), uploadId))
				return
			}
			reject(new UploadPipeError(resp.status, 'complete HTTP ' + resp.status, resp.status >= 400 && resp.status < 500))
		})
	})
}

/** 走完整管线（含断点补传）；成功 resolve UploadOutcome；失败 reject UploadPipeError。
 *  onNewUploadId：新建任务时回调（调用方持久化 upload_id 供重启续传）；
 *  onCompleted：成功完成时回调（调用方清理持久化的 upload_id）。 */
export function runUploadPipeline(
	spec: UploadSpec,
	buildMeta: () => UTSJSONObject,
	onNewUploadId: (uploadId: string) => void,
	onCompleted: (uploadId: string) => void
): Promise<UploadOutcome> {
	return new Promise<UploadOutcome>((resolve, reject) => {
		const finish: (uploadId: string, meta: UTSJSONObject) => void = (uploadId, meta) => {
			completeFor(uploadId, meta).then((out: UploadOutcome) => {
				onCompleted(uploadId)
				resolve(out)
			}, (e: Error) => {
				reject(e)
			})
		}
		const send: (uploadId: string) => void = (uploadId) => {
			putChunkFor(uploadId, spec.filePath).then(() => {
				finish(uploadId, buildMeta())
			}, (e: Error) => {
				reject(e)
			})
		}
		const resumeOrChunk: (uploadId: string) => void = (uploadId) => {
			statusMissing(uploadId).then((missing: boolean) => {
				if (!missing) {
					finish(uploadId, buildMeta())
				} else {
					send(uploadId)
				}
			})
		}
		if (spec.resumeUploadId != '') {
			// 断点：已有 upload_id → 直接 status 补传（应用重启后续传路径）
			resumeOrChunk(spec.resumeUploadId)
			return
		}
		initFor(spec).then((uploadId: string) => {
			onNewUploadId(uploadId)
			resumeOrChunk(uploadId)
		}, (e: Error) => {
			reject(e)
		})
	})
}
