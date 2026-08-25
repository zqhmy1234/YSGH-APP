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
    # 对象存储后端（S5-03）：fake=内存（默认/测试）/ minio=本地模拟 / cos=生产
    storage_backend: str = "fake"
    # 本地文件系统后端根目录（相对 backend/ 解析；2026-08-25 新增，跨进程共享）
    fs_storage_root: str = "data/storage"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "yishu-photos"
    baidu_api_key: str = ""
    baidu_secret_key: str = ""
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

    # Mock 开关：true 时外部 AI 全部走本地 mock（零费用，契约消费方联调用）
    mock_external_ai: bool = True

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
