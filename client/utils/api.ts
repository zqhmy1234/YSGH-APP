/**
 * 网络层封装（B-CL-4）：统一请求（超时/错误码/全局 toast/ApiError 映射）
 *
 * 约定：
 *  - 后端响应信封 {code, message, data}；data 可为对象或数组（UTSJSONObject 字段访问）
 *  - 401 → 自动 refresh（refresh_token 轮换）→ 重放一次；仍失败清 token
 *  - 错误处理：resolve(null) + 全局 toast（UTS Promise 无可靠 catch/onRejected 重载，
 *    本模块永不 reject，调用方判空即可）
 *  - 解析辅助：dataObj() 取对象 data；dataArr() 取数组 data
 */
import { getBaseUrl } from './config'
import { getToken, refreshToken, clearToken } from './auth'
import { captureException } from './sentry'

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

function buildHeader(): UTSJSONObject {
	const header: UTSJSONObject = {
		'Content-Type': 'application/json'
	}
	const token = getToken()
	if (token != '') {
		header.set('Authorization', 'Bearer ' + token)
	}
	return header
}

function doRequest(path: string, method: Method, data: UTSJSONObject | null, retried: boolean): Promise<UTSJSONObject | null> {
	return new Promise<UTSJSONObject | null>((resolve) => {
		uni.request({
			url: getBaseUrl() + path,
			method: method,
			data: data == null ? {} : data,
			header: buildHeader(),
			timeout: REQUEST_TIMEOUT_MS,
			success: (res) => {
				if (res.statusCode === 200) {
					// 2026-08-26（H 建议，集成代劳）：后端不可达/网关返回裸值时 res.data 非对象，
					// 直接强转 UTSJSONObject 会在主线程 FATAL（app 启动崩溃根因）——先守卫再转换
					if (res.data != null && typeof res.data == 'object') {
						resolve(res.data as UTSJSONObject)
					} else {
						resolve(null)
					}
					return
				}
				// 5xx 服务端异常 → Sentry 上报（4xx 业务错误不打扰，噪音闸门）
				if (res.statusCode >= 500) {
					captureException('HTTP ' + res.statusCode + ' ' + path, 'api.http', null)
				}
				const body = (res.data != null && typeof res.data == 'object')
					? (res.data as UTSJSONObject)
					: ({} as UTSJSONObject)
				const err = new ApiError(
					body.getString('code') ?? 'UNKNOWN',
					body.getString('message') ?? ('HTTP ' + res.statusCode),
					res.statusCode
				)
				// 401 → refresh 一次后重放；refresh 失败清 token
				if (res.statusCode === 401 && !retried) {
					refreshToken().then((ok: boolean) => {
						if (ok) {
							doRequest(path, method, data, true).then((b: UTSJSONObject | null) => {
								resolve(b)
							})
						} else {
							clearToken()
							showErrorToast(err)
							resolve(null)
						}
					})
				} else {
					showErrorToast(err)
					resolve(null)
				}
			},
			fail: () => {
				captureException('network fail: ' + path, 'api.network', null)
				showErrorToast(new ApiError('NETWORK', '网络异常，请检查连接', 0))
				resolve(null)
			}
		})
	})
}

/** 统一请求（401 自动刷新重放一次）；失败 resolve(null)，不 reject */
export function request(path: string, method: Method, data: UTSJSONObject | null): Promise<UTSJSONObject | null> {
	return doRequest(path, method, data, false)
}

export function get(path: string): Promise<UTSJSONObject | null> {
	return request(path, 'GET', null)
}

export function post(path: string, data: UTSJSONObject): Promise<UTSJSONObject | null> {
	return request(path, 'POST', data)
}

/** 取信封 data 字段（对象）；body 为空返回 null */
export function dataObj(body: UTSJSONObject): UTSJSONObject | null {
	return body.getJSON('data')
}

/** 取信封 data 字段（数组）；无则空数组 */
export function dataArr(body: UTSJSONObject): Array<UTSJSONObject> {
	const arr = body.getArray('data')
	if (arr == null) {
		return []
	}
	return arr as Array<UTSJSONObject>
}
