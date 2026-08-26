/**
 * 认证封装（B-CL-3）：mock wechat login（code=dev-client）→ token 对
 *
 * 注：2026-08-24 编译排查——插件安全存储函数暂被隔离（编译器 overload panic），
 * 临时用 uni storage；待定位后恢复 EncryptedSharedPreferences（B-CL-3 要求）。
 * 401 自动 refresh（refresh_token 换新对）。
 *
 * 所有函数 resolve-only（boolean），不 reject——UTS Promise.catch 重载限制，
 * 调用方用双参 then 即可。
 */
import { getBaseUrl } from './config'

const KEY_ACCESS = 'yishu.auth.access_token'
const KEY_REFRESH = 'yishu.auth.refresh_token'

/**
 * 设备标识单例（P1-A 对齐）：auth/event_sync/sync_client 统一引用同一常量，
 * 消除 device_id 分裂（'yishu-android-dev' vs 'nova11' 被当成两台设备）。
 * 注意：与 sync_client.ts 的 DEVICE_ID 保持同值；后续统一收敛到单一来源。
 */
export const DEVICE_ID: string = 'yishu-android-dev'

function setSecure(key: string, value: string): void {
	uni.setStorageSync(key, value)
}

function getSecure(key: string): string {
	return uni.getStorageSync(key) as string
}

function removeSecure(key: string): void {
	uni.removeStorageSync(key)
}

export class AuthError extends Error {
	constructor(message: string) {
		super(message)
	}
}

/** 微信 mock 登录（dev 环境 code=dev-client，后端 MOCK 模式签发） */
export function wechatLogin(): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		uni.request({
			url: getBaseUrl() + '/api/v1/auth/wechat',
			method: 'POST',
			data: {
				code: 'dev-client',
				device_id: DEVICE_ID
			},
			success: (res) => {
				if (res.statusCode === 200) {
					const body = res.data as UTSJSONObject
					const data = body.getJSON('data')
					if (data != null) {
						setSecure(KEY_ACCESS, data.getString('access_token') as string)
						setSecure(KEY_REFRESH, data.getString('refresh_token') as string)
						resolve(true)
						return
					}
				}
				resolve(false)
			},
			fail: () => {
				resolve(false)
			}
		})
	})
}

export function getToken(): string {
	return getSecure(KEY_ACCESS)
}

export function getRefreshToken(): string {
	return getSecure(KEY_REFRESH)
}

export function clearToken(): void {
	removeSecure(KEY_ACCESS)
	removeSecure(KEY_REFRESH)
}

/**
 * 确保已登录（幂等）：无 token 时自动登录；返回是否就绪（resolve-only）。
 * 并发去重：App.onLaunch 与页面 onLoad 可能同时调用，共享同一个进行中的登录 Promise。
 */
let _loginInflight: Promise<boolean> | null = null

export function ensureLogin(): Promise<boolean> {
	const token = getToken()
	if (token != '') {
		return Promise.resolve(true)
	}
	let p = _loginInflight
	if (p == null) {
		p = wechatLogin()
		_loginInflight = p
		p.then((ok: boolean) => {
			_loginInflight = null
		})
	}
	return p
}

/** refresh_token 换新（401 触发）；resolve-only */
export function refreshToken(): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		const rt = getRefreshToken()
		if (rt == '') {
			// 无 refresh_token → 重新登录
			wechatLogin().then((ok: boolean) => {
				resolve(ok)
			})
			return
		}
		uni.request({
			url: getBaseUrl() + '/api/v1/auth/refresh',
			method: 'POST',
			data: {
				refresh_token: rt,
				device_id: DEVICE_ID
			},
			success: (res) => {
				if (res.statusCode === 200) {
					const body = res.data as UTSJSONObject
					const data = body.getJSON('data')
					if (data != null) {
						setSecure(KEY_ACCESS, data.getString('access_token') as string)
						setSecure(KEY_REFRESH, data.getString('refresh_token') as string)
						resolve(true)
						return
					}
				}
				// 刷新失败 → 清 token
				clearToken()
				resolve(false)
			},
			fail: () => {
				clearToken()
				resolve(false)
			}
		})
	})
}
