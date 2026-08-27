/**
 * 画像管理页 · 端点常量（Agent A1 页面域，本地兜底）
 *
 * 契约 C6：A2（单写者）将在 client/utils/contract.ts 追加 PATH_PROFILE_SENSITIVE 等常量。
 * 并行隔离期 develop 分支尚未合入该常量，本页为保独立编译使用页面级兜底常量；
 * 值与契约一致，集成时可无缝切换为 `import { PATH_PROFILE_SENSITIVE } from '@/utils/contract'`。
 */
export const PATH_PROFILE_SENSITIVE: string = '/api/v1/profile/sensitive'
