/**
 * 认证封装（B-CL-3）：mock wechat login（code=dev-client）→ token 对
 *
 * ⚠️ TD-P3 M5（审查中危）登记——安全存储降级说明（2026-08-26）：
 *   理想实现：access/refresh token 走 EncryptedSharedPreferences（B-CL-3 要求），
 *   插件接口已在 uni_modules/yishu-photo-watch/utssdk/interface.uts 声明
 *   （安全存储随自定义基座波次恢复——依赖 androidx.security 三方库）。
 *   当前状态：因 2026-08-24 编译器 overload panic，安全存储函数被隔离，
 *   临时降级为 uni storage 明文落盘（含 30 天 refresh_token）。
 *   风险：rooted 设备 / 系统备份 / 同机恶意应用可读取 → 长会话劫持。
 *   既有缓解：① refresh_token 绑定 device_id（后端 devices 表可吊销、
 *   轮换即作废旧 token，AUTH-005/006）；② access 仅 2h 短时。
 *   恢复路径：自定义基座就绪后，将 setSecure/getSecure/removeSecure 改走
 *   EncryptedSharedPreferences 实现（无需改本文件其他逻辑）。
 *
 * 401 自动 refresh（refresh_token 换新对）——G1/R6#6：single-flight 共享 in-flight，
 * 并发 401 只触发一次 refresh（其余 await 同一 Promise），消除并发双轮换竞态。
 *
 * 所有函数 resolve-only（boolean），不 reject——UTS Promise.catch 重载限制，
 * 调用方用双参 then 即可。
 */
import { getBaseUrl } from './config'

const KEY_ACCESS = 'yishu.auth.access_token'
const KEY_REFRESH = 'yishu.auth.refresh_token'

/**
 * 设备标识单例（P1-A 对齐 · 2026-08-27 C1 收口 R3 O3）：全客户端唯一导出源，
 * event_sync/sync_client 统一 import 此常量，消除 device_id 分裂
 * （'yishu-android-dev' vs 'nova11' 被当成两台设备的历史事故）。
 */
export const DEVICE_ID: string = 'yishu-android-dev'

// 降级实现（TD-P3 M5 登记）：uni storage 明文；恢复 EncryptedSharedPreferences 见文件头说明。
// 统一入口封装成 *Secure，恢复时只改这三个函数即可。
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

/** refresh_token 换新（401 触发）；resolve-only
 *
 * G1/R6#6（single-flight）：并发 401 只触发一次 refresh——
 * 模块级 _refreshInflight 共享进行中 Promise，其余调用 await 同一 Promise；
 * 落定后清除（无论成功/失败），下次重新执行。消除并发双轮换竞态
 * （后端 refresh 轮换为原子 single-use，双轮换必有一个 401）。
 */
let _refreshInflight: Promise<boolean> | null = null

export function refreshToken(): Promise<boolean> {
	let p = _refreshInflight
	if (p != null) {
		return p
	}
	p = doRefresh()
	_refreshInflight = p
	p.then((ok: boolean) => {
		_refreshInflight = null
	})
	return p
}

function doRefresh(): Promise<boolean> {
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

/**
 * 退出登录（G1/R6#7）：调后端 logout/revoke（吊销 devices 表 refresh，AUTH-006）
 * 后清本地凭据。幂等：后端 token 无效/网络失败也返回 true——会话结束优先，
 * 服务端吊销失败由 refresh 30 天 TTL 兜底。resolve-only。
 */
export function logout(): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		uni.request({
			url: getBaseUrl() + '/api/v1/auth/logout',
			method: 'POST',
			data: {
				refresh_token: getRefreshToken()
			},
			success: () => {
				clearToken()
				resolve(true)
			},
			fail: () => {
				clearToken()
				resolve(true)
			}
		})
	})
}
