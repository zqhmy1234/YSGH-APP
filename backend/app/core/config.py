"""忆述光华 backend · 应用配置（pydantic-settings）"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 固定位于 backend/ 下（无论从仓库根还是 backend/ 启动都能加载）
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """统一配置中心，读取 backend/.env（参考 .env.example）"""

    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 环境
    app_env: str = "development"
    debug: bool = True
    # CORS 白名单（逗号分隔；审查修复 P1-05：生产默认空=同源，开发放开）
    cors_origins: str = ""

    # 服务
    host: str = "0.0.0.0"
    port: int = 8000
    base_url: str = "http://localhost:8000"

    # 数据库
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/yishu"

    # Redis / RQ
    redis_url: str = "redis://localhost:6379/0"

    # Qdrant 向量库（B2；修复：原代码硬编码 localhost，部署/CI 不可切换）
    qdrant_url: str = "http://localhost:6333"

    # JWT（决策 #8；密钥 ≥32 字节，否则 pyjwt 告警）
    jwt_secret: str = "change-me-32-bytes-min-secret-0000"
    jwt_access_ttl_minutes: int = 120
    jwt_refresh_ttl_days: int = 30
    jwt_algorithm: str = "HS256"

    # G1/R6#8（认证安全）：refresh_token 哈希用独立 HMAC 密钥（与 jwt_secret 隔离）。
    # 即使 JWT 签名密钥泄漏，也无法用其伪造 devices 表 refresh_token 哈希；
    # 生产默认值强制替换（见 _apply_production_safety）。
    refresh_token_hmac_key: str = "change-me-refresh-hmac-key-0000000000"

    # 外部 API Key
    dashscope_api_key: str = ""
    # 百炼业务空间 ID（sk-ws- 工作空间级 key 必须带 X-DashScope-WorkSpace，SDK 无环境变量兜底）
    dashscope_workspace_id: str = ""
    # workspace 专属 Host 的地域；完整地址仍可用 DASHSCOPE_BASE_URL 覆盖。
    dashscope_region: str = Field("cn-beijing", pattern=r"^[a-z0-9-]+$")
    dashscope_base_url: str = ""
    # auto=主通道已有情绪则跳过本地；always=强制本地覆盖；off=关闭本地增强。
    asr_local_emotion_mode: Literal["auto", "always", "off"] = "auto"
    # 生产必须指向部署阶段预置的 SenseVoice 目录，避免首个请求联网下载。
    sensevoice_model_dir: str = ""
    # 腾讯云子账号密钥：别名读取（2026-08-19 对齐 Infisical 命名）——
    # 优先 TENCENT_SECRET_ID/TENCENT_SECRET_KEY（backend/.env 现状），
    # 回退 TENCENT_CI_SECRET_ID/TENCENT_GUANHAIFENG_CI_SECRET_KEY（Infisical 存量名）。
    tencent_secret_id: str = Field(
        "", validation_alias=AliasChoices("TENCENT_SECRET_ID", "TENCENT_CI_SECRET_ID")
    )
    tencent_secret_key: str = Field(
        "", validation_alias=AliasChoices("TENCENT_SECRET_KEY", "TENCENT_GUANHAIFENG_CI_SECRET_KEY")
    )
    cos_bucket: str = Field("", validation_alias=AliasChoices("COS_BUCKET", "TENCENT_COS_BUCKET"))
    cos_region: str = Field("", validation_alias=AliasChoices("COS_REGION", "TENCENT_COS_REGION"))
    # 腾讯云业务标识（非敏感，公开参数）
    tencent_appid: str = ""
    tencent_sts_role_arn: str = ""
    # 对象存储后端（S5-03）：fake=内存（默认/测试）/ fs=本地文件系统（跨进程共享，
    # 根目录 FS_STORAGE_ROOT）/ minio=本地模拟 / cos=生产（腾讯云 COS）
    storage_backend: Literal["fake", "fs", "minio", "cos"] = "fake"
    # 本地文件系统后端根目录（相对 backend/ 解析；2026-08-25 新增，跨进程共享）
    fs_storage_root: str = "data/storage"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "yishu-photos"
    baidu_api_key: str = Field(
        "", validation_alias=AliasChoices("BAIDU_API_KEY", "BAIDU_OCR_API_KEY")
    )
    baidu_secret_key: str = Field(
        "", validation_alias=AliasChoices("BAIDU_SECRET_KEY", "BAIDU_OCR_SECRET_KEY")
    )
    # 高德逆地理（外部API清单 #5）：别名读取——优先 AMAP_API_KEY（.env 惯例），
    # 回退 AMAP_WEB_API_KEY（Infisical 存量名，2026-08-25 已存）
    amap_api_key: str = Field(
        "", validation_alias=AliasChoices("AMAP_API_KEY", "AMAP_WEB_API_KEY")
    )

    # 可观测
    sentry_dsn: str = ""

    # 企业微信客服回调（F6；未配置时回调验签必拒，配置后启用）
    wechat_corp_id: str = ""
    wechat_token: str = ""
    wechat_encoding_aes_key: str = ""
    # 微信开放平台 code2session 登录（决策 #8；Wave4-L 接入，替换 mock unionid）——
    # 未配置时 dev/test 走 mock、production 保持 501（不静默降级 mock 登录）。
    wechat_appid: str = ""
    wechat_secret: str = ""

    # 用户内容安全审核（B5b #8 · Wave4-L）：适配器开关
    #   tencent_ci = 当前顶替（文本=规则预检+护栏 / 图片=CI image_audit）
    #   aliyun     = 上架前启用（阿里云内容安全增强版，需 AccessKey + 开通「内容安全」服务）
    #   off        = 不调外部审核（文本仅本地规则、图片默认放行；调用方自行决定）
    content_safety_provider: Literal["tencent_ci", "aliyun", "off"] = "tencent_ci"
    # 阿里云内容安全（Green）AccessKey——⚠️ 不是百炼 DashScope key，需阿里云账号
    # AccessKey + 开通「内容安全」服务（2026-08-26 监控确认：百炼 key ≠ 内容安全 key）。
    # 别名读取兼容 Infisical 存量名。
    aliyun_access_key_id: str = Field(
        "", validation_alias=AliasChoices("ALIYUN_ACCESS_KEY_ID", "ALIYUN_AK_ID")
    )
    aliyun_access_key_secret: str = Field(
        "", validation_alias=AliasChoices("ALIYUN_ACCESS_KEY_SECRET", "ALIYUN_AK_SECRET")
    )
    aliyun_content_safety_region: str = "cn-beijing"

    # Mock 开关：true 时外部 AI 全部走本地 mock（零费用，契约消费方联调用）。
    # 2026-08-26（P1-A 配置对齐）：生产安全默认改 false（fail-closed——漏配环境变量的
    # 新部署若沿用默认 true，短信验证码 mock 直返会造成任意手机号账户接管）。
    # dev/CI 联调可显式 MOCK_EXTERNAL_AI=true 覆盖；测试套件由 conftest autouse 强制 true。
    mock_external_ai: bool = False

    # 双层 Rerank 第一层：bge-reranker 本地模型路径（WP-F；目录不存在则跳过重排）
    # 审查修复(P1-10)：存纯文件名（相对 backend/models），加载侧用 __file__ 解析绝对路径，
    # 消除对 CWD 的依赖（原 "backend/models/..." 仅当 CWD=仓库根才有效）。
    reranker_model: str = "bge-reranker-v2-m3"  # 2026-08-20: 设计指定版（base 留档可回退）
    # 重排低相关过滤阈值（0=不过滤；bge-reranker sigmoid 分数，实测定标）
    rerank_min_score: float = 0.0
    # 2026-08-25 RAG 审查：rerank 默认关闭。实测 CPU 上 bge-reranker-v2-m3
    # 单对 ~850ms——50 候选一次搜索 ~40s，远超 P95<3s 门禁；且 rerank 只重排
    # 候选集内文档，救不了未进 top-50 的相关文档（描述性查询失效根因在召回层）。
    # GPU 部署 / 延迟预算允许时置 true 启用（候选数受 rerank_max_candidates 限制）。
    rerank_enabled: bool = False
    # Wave2-F（2026-08-26）：第一层 reranker 自动启用策略。
    # rerank_auto_enable=True 时运行时自检：torch.cuda（GPU）可用 且 reranker 模型
    # 就绪 → 等效 rerank_enabled=True；CPU / 模型缺失 → 保持关闭（实测 CPU 单对
    # ~850ms × 20 候选 ≈ 17s 超 P95<3s 门禁）。rerank_enabled 显式置 true 时忽略
    # 此开关（显式优先）。
    # 门禁文档化：启用前提 = 部署机 P95<3s 探针通过（GPU 推理 + 候选数受限时可满足）；
    # 未过探针的机器必须保持关闭，以防搜索延迟突破 M1 门禁。
    rerank_auto_enable: bool = False
    # 重排候选上限（防 CPU 延迟爆表；默认 20 → ~17s，仍超门禁，仅建议 GPU 用）
    rerank_max_candidates: int = 20
    # 第二层 LLM 精排（Wave2-F 2026-08-26，B2-1 Ilya 方案）：
    # 总开关默认开，运行时再按 llm_available（无 key / mock → 原序返回）自门控。
    # 管线：bge 粗排 top-50→top-10（rerank_llm_candidates）→ qwen-flash 判"能否
    # 回答" → top-5（rerank_llm_top_k）。
    # 门禁文档化：LLM 精排 = 一次 qwen-flash 往返（10 候选一个 batch，实测 ~1-2s），
    # 延迟预算紧张时置 false；真实 key 联调期建议先人工验证 P95<3s 再默认开。
    rerank_llm_enabled: bool = True
    rerank_llm_candidates: int = 10
    rerank_llm_top_k: int = 5
    # P1-A 类目路由（2026-08-25）：描述性查询按规则词表分类 → content_class 过滤，
    # 把干扰类挡在召回路外（修复 descriptive 层 hit_rate@3=0.5 的召回缺口）。
    # 规则词表确定性/零延迟；无主导类别不过滤；空结果自动回退全量。
    class_routing_enabled: bool = True
    # P0-A LLM 改写/路由总开关（2026-08-25 调研后默认开）：
    # 改法三件套——①prompt v2 有门控（短关键词原样返回，只改错字/口语/描述性）；
    # ②双路召回原查询路用生效过滤器（eff_filters，此前误用回退前 filters 恒空）；
    # ③类目路由跑原始查询（类别不随改写漂移）。
    # 实测参考：无门控替换式改写 EXT recall 0.886→0.75（改写伤害），门控+双路后应恢复。
    llm_rewrite_enabled: bool = True

    # G1/R6#2/#3（认证安全 · 通用限流）：消费方 backend/app/core/ratelimit.py
    #   覆盖域：auth / asr（含 /api/v1/guard）/ search（先覆盖三域，其余域按需登记）
    #   维度：client_ip + user（Authorization Bearer 解析；无/坏 token 仅 IP 维度）
    #   窗口：固定窗口 INCR+EXPIRE（rate_limit_window 秒）；Redis 故障降级进程内计数，不 500
    #   白名单：rate_limit_whitelist（逗号分隔 IP）直接放行；
    #   rate_limit_trust_proxy=True 时优先 X-Forwarded-For（仅部署在可信反向代理后开启）
    rate_limit_enabled: bool = True
    rate_limit_trust_proxy: bool = False
    rate_limit_whitelist: str = ""
    rate_limit_window: int = 60
    rate_limit_auth_ip: int = 120          # auth 域：同 IP / 窗口
    rate_limit_auth_user: int = 300        # auth 域：同用户 / 窗口
    rate_limit_asr_ip: int = 20            # asr+guard 域：同 IP / 窗口
    rate_limit_asr_user: int = 40          # asr+guard 域：同用户 / 窗口
    rate_limit_search_ip: int = 60         # search 域：同 IP / 窗口
    rate_limit_search_user: int = 120      # search 域：同用户 / 窗口


def _apply_production_safety(settings: Settings) -> Settings:
    """生产环境安全兜底（P0-1，审查 H1/S4-四-3）：

    1. JWT：禁止默认密钥（公开字符串，任何人都可伪造 token）——dev/test 保留默认值。
    2. Refresh HMAC：禁止默认密钥（G1/R6#8——与 jwt_secret 隔离的 refresh 哈希密钥，
       DB 泄漏场景下防止攻击者用公开默认值离线爆破/构造哈希）。
    3. Mock：强制 mock_external_ai=False——漏配环境变量的新部署若沿用默认 True，
       短信验证码 mock 直返会造成任意手机号账户接管（认证绕过），必须 fail-closed。
    """
    import logging

    if settings.app_env != "production":
        return settings
    if settings.jwt_secret == "change-me-32-bytes-min-secret-0000":
        raise RuntimeError(
            "生产环境必须配置 JWT_SECRET（当前为默认值，存在伪造 token 风险）——"
            "请在 backend/.env 设置强随机密钥（≥32 字节）"
        )
    if settings.refresh_token_hmac_key == "change-me-refresh-hmac-key-0000000000":
        raise RuntimeError(
            "生产环境必须配置 REFRESH_TOKEN_HMAC_KEY（当前为默认值）——"
            "请设置与 JWT_SECRET 独立的强随机密钥（HMAC-SHA256 密钥隔离）"
        )
    if settings.mock_external_ai:
        logging.getLogger("yishu.config").warning(
            "生产环境强制关闭 mock_external_ai（防 mock 验证码/假凭证直返）——"
            "请在 backend/.env 显式设置 MOCK_EXTERNAL_AI=false"
        )
        settings.mock_external_ai = False
    return settings


@lru_cache
def get_settings() -> Settings:
    return _apply_production_safety(Settings())


settings = get_settings()
