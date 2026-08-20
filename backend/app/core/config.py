"""忆述光华 backend · 应用配置（pydantic-settings）"""
from functools import lru_cache
from pathlib import Path

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

    # 外部 API Key
    dashscope_api_key: str = ""
    # 百炼业务空间 ID（sk-ws- 工作空间级 key 必须带 X-DashScope-WorkSpace，SDK 无环境变量兜底）
    dashscope_workspace_id: str = ""
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
    # 对象存储后端（S5-03）：fake=内存（默认/测试）/ minio=本地模拟 / cos=生产
    storage_backend: str = "fake"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "yishu-photos"
    baidu_api_key: str = ""
    baidu_secret_key: str = ""
    amap_api_key: str = ""

    # 可观测
    sentry_dsn: str = ""

    # 企业微信客服回调（F6；未配置时回调验签必拒，配置后启用）
    wechat_corp_id: str = ""
    wechat_token: str = ""
    wechat_encoding_aes_key: str = ""

    # Mock 开关：true 时外部 AI 全部走本地 mock（零费用，契约消费方联调用）
    mock_external_ai: bool = True

    # 双层 Rerank 第一层：bge-reranker 本地模型路径（WP-F；目录不存在则跳过重排）
    # 审查修复(P1-10)：存纯文件名（相对 backend/models），加载侧用 __file__ 解析绝对路径，
    # 消除对 CWD 的依赖（原 "backend/models/..." 仅当 CWD=仓库根才有效）。
    reranker_model: str = "bge-reranker-v2-m3"  # 2026-08-20: 设计指定版（base 留档可回退）
    # 重排低相关过滤阈值（0=不过滤；bge-reranker sigmoid 分数，实测定标）
    rerank_min_score: float = 0.0


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # 安全修复（审查 CRITICAL）：生产环境禁止使用默认 JWT 密钥（公开字符串，
    # 任何人都可伪造 token）。dev/test 保留默认值保证本地可跑。
    if settings.app_env == "production" and settings.jwt_secret == "change-me-32-bytes-min-secret-0000":
        raise RuntimeError(
            "生产环境必须配置 JWT_SECRET（当前为默认值，存在伪造 token 风险）——"
            "请在 backend/.env 设置强随机密钥（≥32 字节）"
        )
    return settings


settings = get_settings()
