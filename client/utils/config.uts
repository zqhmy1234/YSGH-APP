/**
 * 环境配置（B-CL-2）：baseURL 开关 + mock 开关
 *
 *  - dev 模拟器：10.0.2.2:8000（Android 模拟器访问宿主机）
 *  - dev 真机：填 REAL_DEVICE_HOST 为本机局域网 IP（真机与后端同网段）
 *  - prod：生产域名（合规/部署后替换）
 */
export const ENV: string = 'dev' // 'dev' | 'prod'

/** Sentry DSN（可观测双通道·客户端侧；DSN 为公开标识，Sentry 官方允许客户端明文内嵌——非机密） */
export const SENTRY_DSN: string = 'https://dfa9a0ac9011d7d84da919ae87c2cd60@o4511955023888384.ingest.us.sentry.io/4511958507388928'

/** 真机联调后端地址：
 *  - 'localhost'：经 adb reverse USB 隧道访问本机（最稳，绕开 WiFi 段/防火墙；需 adb reverse tcp:8000 tcp:8000）
 *  - 局域网 IP：直接 WiFi 访问（依赖同网段 + 本机防火墙放行 8000）
 *  - 留空：走模拟器地址 10.0.2.2
 */
export const REAL_DEVICE_HOST: string = 'localhost'

const DEV_BASE_URL: string = 'http://10.0.2.2:8000'
const PROD_BASE_URL: string = 'https://api.yishuguanghua.example.com'

/** 外部 AI mock 开关（与后端 MOCK_EXTERNAL_AI 对齐；生产必须 false） */
export const MOCK_EXTERNAL_AI: boolean = true

/** 调试：启动后自动进入 AGG-016 一致性自检页（仅 dev；验证完置回 false） */
export const AGG_CHECK_ON_DEVICE: boolean = false

/** dev 调试页常驻开关（O20）：非 dev 构建（ENV='prod'）自动关闭——
 *  值为 ENV 派生，生产构建无需人工改；dev 构建下 profile"关于"区展示调试入口。
 *  注意：AGG_CHECK_ON_DEVICE 是"启动自动进页"的一次性开关，本开关是"页面/入口常驻"总闸。 */
export const DEV_PAGE_ENABLED: boolean = ENV === 'dev'

export function getBaseUrl(): string {
	if (ENV === 'prod') {
		return PROD_BASE_URL
	}
	if (REAL_DEVICE_HOST == 'localhost') {
		// adb reverse USB 隧道（真机联调最稳路径）
		return 'http://127.0.0.1:8000'
	}
	if (REAL_DEVICE_HOST != '') {
		return 'http://' + REAL_DEVICE_HOST + ':8000'
	}
	return DEV_BASE_URL
}
