/**
 * 认证封装（B-CL-3）：mock wechat login（code=dev-client）→ token 对
 *
 * token 落盘走 UTS 插件 SecurePrefs（EncryptedSharedPreferences），
 * 不落明文 uni storage。401 自动 refresh（refresh_token 换新对）。
 */
import { getBaseUrl } from './config'
import {
	setSecureString,
	getSecureString,
	removeSecureString
} from '@/uni_modules/yishu-photo-watch/utssdk/interface.uts'

const KEY_ACCESS = 'yishu.auth.access_token'
const KEY_REFRESH = 'yishu.auth.refresh_token'

export class AuthError extends Error {
	constructor(message: string) {
		super(message)
	}
}

/** 微信 mock 登录（dev 环境 code=dev-client，后端 MOCK 模式签发） */
export function wechatLogin(): Promise<boolean> {
	return new Promise<boolean>((resolve, reject) => {
		uni.request({
			url: getBaseUrl() + '/api/v1/auth/wechat',
			method: 'POST',
			data: {
				code: 'dev-client',
				device_id: 'yishu-android-dev'
			},
			success: (res) => {
				if (res.statusCode === 200) {
					const body = res.data as UTSJSON
					const data = body.getJSON('data')
					if (data == null) {
						reject(new AuthError('登录响应缺 data'))
						return
					}
					setSecureString(KEY_ACCESS, data.getString('access_token') as string)
					setSecureString(KEY_REFRESH, data.getString('refresh_token') as string)
					resolve(true)
				} else {
					reject(new AuthError('登录失败 HTTP ' + res.statusCode))
				}
			},
			fail: (err) => {
				reject(new AuthError('网络错误 ' + JSON.stringify(err)))
			}
		})
	})
}

export function getToken(): string {
	return getSecureString(KEY_ACCESS)
}

export function getRefreshToken(): string {
	return getSecureString(KEY_REFRESH)
}

export function clearToken(): void {
	removeSecureString(KEY_ACCESS)
	removeSecureString(KEY_REFRESH)
}

/**
 * 确保已登录（幂等）：无 token 或 token 过期时自动登录/刷新。
 * App 启动与请求前调用；返回是否就绪。
 */
export function ensureLogin(): Promise<boolean> {
	return new Promise<boolean>((resolve, reject) => {
		const token = getToken()
		if (token != '') {
			resolve(true)
			return
		}
		wechatLogin().then((ok: boolean) => {
			resolve(ok)
		}).catch((err: Error | null) => {
			reject(err)
		})
	})
}

/** refresh_token 换新（401 触发） */
export function refreshToken(): Promise<boolean> {
	return new Promise<boolean>((resolve, reject) => {
		const rt = getRefreshToken()
		if (rt == '') {
			// 无 refresh_token → 重新登录
			wechatLogin().then((ok: boolean) => resolve(ok)).catch((err: Error | null) => reject(err))
			return
		}
		uni.request({
			url: getBaseUrl() + '/api/v1/auth/refresh',
			method: 'POST',
			data: {
				refresh_token: rt,
				device_id: 'yishu-android-dev'
			},
			success: (res) => {
				if (res.statusCode === 200) {
					const body = res.data as UTSJSON
					const data = body.getJSON('data')
					if (data == null) {
						reject(new AuthError('刷新响应缺 data'))
						return
					}
					setSecureString(KEY_ACCESS, data.getString('access_token') as string)
					setSecureString(KEY_REFRESH, data.getString('refresh_token') as string)
					resolve(true)
				} else {
					// 刷新失败 → 清 token 重登
					clearToken()
					reject(new AuthError('刷新失败 HTTP ' + res.statusCode))
				}
			},
			fail: (err) => {
				reject(new AuthError('刷新网络错误 ' + JSON.stringify(err)))
			}
		})
	})
}
