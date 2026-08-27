#!/usr/bin/env node
/**
 * G1/R6#6 认证安全 · refresh single-flight 并发单测（真实导入 client/utils/auth.ts）
 *
 * 背景：本仓库无客户端 JS 测试基建（uni-app x 无 package.json/vitest），
 * 采用 Node 24 原生类型剥离（Type Stripping）+ registerHooks 补 .ts 扩展名解析，
 * 直接加载**真实** auth.ts 验证并发 401 → 只触发一次 refresh（其余 await 同一 in-flight）。
 *
 * 运行：node --test scripts/test_auth_singleflight.mjs
 */
import { registerHooks } from 'node:module'
import { existsSync } from 'node:fs'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { test } from 'node:test'
import assert from 'node:assert/strict'

// auth.ts 内部 `import ... from './config'` 无扩展名——Node ESM 不猜扩展名，
// registerHooks 给相对无扩展名导入补 .ts（Node 类型剥离可执行 TS）。
registerHooks({
	resolve(specifier, context, nextResolve) {
		if (
			(specifier.startsWith('./') || specifier.startsWith('../')) &&
			!/\.[a-z0-9]+$/i.test(specifier)
		) {
			const base = fileURLToPath(new URL(specifier, context.parentURL))
			if (existsSync(base + '.ts')) {
				return nextResolve(pathToFileURL(base + '.ts').href, context)
			}
		}
		return nextResolve(specifier, context)
	}
})

const CLIENT_UTILS = pathToFileURL(
	fileURLToPath(new URL('../client/utils/', import.meta.url))
)

/** uni 全局桩：storage + request（异步回调，模拟真实网络往返）
 *
 * auth.ts 用 UTSJSONObject 方法（getJSON/getString）解析响应——Node 桩需模拟，
 * 否则真对象没有这些方法。
 */
function installUniStub() {
	const store = {}
	const uni = {
		_requestCount: 0,
		_requestCalls: [],
		setStorageSync(k, v) {
			store[k] = v
		},
		getStorageSync(k) {
			return store[k] ?? ''
		},
		removeStorageSync(k) {
			delete store[k]
		},
		request(opts) {
			this._requestCount++
			this._requestCalls.push(opts)
			setTimeout(() => {
				if (opts.success) {
					const inner = {
						access_token: 'new-access',
						refresh_token: 'new-refresh',
						getString(k) {
							return this[k]
						}
					}
					const body = {
						data: inner,
						getJSON(k) {
							return this[k]
						}
					}
					opts.success({ statusCode: 200, data: body })
				}
			}, 20)
		}
	}
	globalThis.uni = uni
	return uni
}

let mod
test('setup: import real client/utils/auth.ts', async () => {
	installUniStub()
	mod = await import(new URL('./auth.ts', CLIENT_UTILS).href)
	assert.equal(typeof mod.refreshToken, 'function')
	assert.equal(typeof mod.logout, 'function')
})

test('refresh single-flight: 并发 401 → 只触发一次 /auth/refresh', async () => {
	const uni = installUniStub()
	// 重新导入以重置模块级 _refreshInflight（同模块缓存已含上用例状态，直接复用亦可——
	// 上用例未发起 refresh，_refreshInflight 为 null）
	mod = await import(new URL('./auth.ts', CLIENT_UTILS).href)
	uni.setStorageSync('yishu.auth.refresh_token', 'rt-1')
	uni.setStorageSync('yishu.auth.access_token', 'acc-1')

	const results = await Promise.all([
		mod.refreshToken(),
		mod.refreshToken(),
		mod.refreshToken(),
		mod.refreshToken(),
		mod.refreshToken()
	])

	// 5 个并发调用只发出 1 次 refresh 请求（single-flight 共享 in-flight）
	assert.equal(uni._requestCount, 1, '并发 401 应只触发一次 refresh 请求')
	assert.match(uni._requestCalls[0].url, /\/api\/v1\/auth\/refresh$/)
	// 全部等待方拿到同一成功结果
	assert.deepEqual(results, [true, true, true, true, true])
	// 成功后本地写回新 token 对
	assert.equal(uni.getStorageSync('yishu.auth.refresh_token'), 'new-refresh')
	assert.equal(uni.getStorageSync('yishu.auth.access_token'), 'new-access')
})

test('refresh single-flight: 顺序两次调用各自独立（落定后清 in-flight）', async () => {
	const uni = installUniStub()
	mod = await import(new URL('./auth.ts', CLIENT_UTILS).href)
	uni.setStorageSync('yishu.auth.refresh_token', 'rt-2')

	await mod.refreshToken()
	await mod.refreshToken()
	assert.equal(uni._requestCount, 2, '先后两次（非并发）应各触发一次 refresh')
})

test('logout: 调 /auth/logout + 清本地凭据', async () => {
	const uni = installUniStub()
	mod = await import(new URL('./auth.ts', CLIENT_UTILS).href)
	uni.setStorageSync('yishu.auth.refresh_token', 'rt-3')
	uni.setStorageSync('yishu.auth.access_token', 'acc-3')

	const ok = await mod.logout()
	assert.equal(ok, true)
	assert.equal(uni._requestCount, 1)
	assert.match(uni._requestCalls[0].url, /\/api\/v1\/auth\/logout$/)
	// 本地凭据已清（clearToken）
	assert.equal(uni.getStorageSync('yishu.auth.refresh_token'), '')
	assert.equal(uni.getStorageSync('yishu.auth.access_token'), '')
})
