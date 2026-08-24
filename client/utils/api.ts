/**
 * 网络层封装（B-CL-4）：统一请求（超时/错误码/全局 toast/ApiError 映射）
 *
 * 约定：
 *  - 后端响应信封 {code, message, data}；data 可为对象或数组
 *  - 401 → 自动 refresh（refresh_token 轮换）→ 重放一次；仍失败清 token
 *  - 网络/超时/业务错误统一抛 ApiError，UI 层可选择性 toast
 *  - 解析辅助：dataObj() 取对象 data；dataArr() 取数组 data（UTSJSON 数组根不可靠，信封字段安全）
 */
import { getBaseUrl } from './config'
import { getToken, refreshToken, clearToken } from './auth'

export class ApiError extends Error {
	code: string
	httpStatus: number

	constructor(code: string, message: string, httpStatus: number) {
		super(message)
		this.code = code
		this.httpStatus = httpStatus
	}
}

export type Method = 'GET' | 'POST' | 'PUT' | 'DELETE'

const REQUEST_TIMEOUT_MS: number = 15000

/** 统一错误 toast（业务错误码也提示，方便第一波联调定位） */
export function showErrorToast(err: Error | null): void {
	const msg = err != null && err.message != '' ? err.message : '请求失败'
	uni.showToast({
		title: msg,
		icon: 'none',
		duration: 2500
	})
}

function buildHeader(): UTSJSON {
	const header: UTSJSON = {
		'Content-Type': 'application/json'
	}
	const token = getToken()
	if (token != '') {
		header.set('Authorization', 'Bearer ' + token)
	}
	return header
}

function doRequest(path: string, method: Method, data: UTSJSON | null): Promise<UTSJSON> {
	return new Promise<UTSJSON>((resolve, reject) => {
		uni.request({
			url: getBaseUrl() + path,
			method: method,
			data: data == null ? {} : data,
			header: buildHeader(),
			timeout: REQUEST_TIMEOUT_MS,
			success: (res) => {
				const body = res.data as UTSJSON
				if (res.statusCode === 200) {
					resolve(body)
					return
				}
				const code = body.getString('code') ?? 'UNKNOWN'
				const message = body.getString('message') ?? ('HTTP ' + res.statusCode)
				reject(new ApiError(code, message, res.statusCode))
			},
			fail: (err) => {
				reject(new ApiError('NETWORK', '网络异常：' + JSON.stringify(err), 0))
			}
		})
	})
}

/** 带 401 自动刷新重放的统一请求；resolve 完整信封（解析辅助见 dataObj/dataArr） */
export function request(path: string, method: Method, data: UTSJSON | null, retried: boolean = false): Promise<UTSJSON> {
	return new Promise<UTSJSON>((resolve, reject) => {
		doRequest(path, method, data).then((body: UTSJSON) => {
			resolve(body)
		}).catch((err: Error | null) => {
			const apiErr = err as ApiError
			if (apiErr.httpStatus === 401 && !retried) {
				refreshToken().then((ok: boolean) => {
					if (ok) {
						request(path, method, data, true).then((b: UTSJSON) => resolve(b)).catch((e: Error | null) => reject(e))
					} else {
						clearToken()
						reject(err)
					}
				}).catch((e: Error | null) => {
					clearToken()
					reject(e)
				})
			} else {
				reject(err)
			}
		})
	})
}

export function get(path: string): Promise<UTSJSON> {
	return request(path, 'GET', null, false)
}

export function post(path: string, data: UTSJSON): Promise<UTSJSON> {
	return request(path, 'POST', data, false)
}

/** 取信封 data 字段（对象） */
export function dataObj(body: UTSJSON): UTSJSON | null {
	return body.getJSON('data')
}

/** 取信封 data 字段（数组） */
export function dataArr(body: UTSJSON): Array<UTSJSON> {
	const arr = body.getArray('data')
	if (arr == null) {
		return []
	}
	return arr as Array<UTSJSON>
}
