/**
 * 环境配置（B-CL-2）：baseURL 开关 + mock 开关
 *
 *  - dev 模拟器：10.0.2.2:8000（Android 模拟器访问宿主机）
 *  - dev 真机：填 REAL_DEVICE_HOST 为本机局域网 IP（真机与后端同网段）
 *  - prod：生产域名（合规/部署后替换）
 */
export const ENV: string = 'dev' // 'dev' | 'prod'

/** 真机联调：本机后端局域网 IP（留空则走模拟器地址）——nova 11 联调 2026-08-24 */
export const REAL_DEVICE_HOST: string = '192.168.31.165'

const DEV_BASE_URL: string = 'http://10.0.2.2:8000'
const PROD_BASE_URL: string = 'https://api.yishuguanghua.example.com'

/** 外部 AI mock 开关（与后端 MOCK_EXTERNAL_AI 对齐；生产必须 false） */
export const MOCK_EXTERNAL_AI: boolean = true

export function getBaseUrl(): string {
	if (ENV === 'prod') {
		return PROD_BASE_URL
	}
	if (REAL_DEVICE_HOST != '') {
		return 'http://' + REAL_DEVICE_HOST + ':8000'
	}
	return DEV_BASE_URL
}
