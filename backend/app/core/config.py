"""忆述光华 backend · 应用配置（pydantic-settings）"""
import logging
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

    # ── LLM 供应商选择（2026-10-15 · 台账 §7.27）──────────────────────────────
    # 背景：当日实测机器上一把过期的 `DASHSCOPE_API_KEY`（用户级环境变量）**顶掉了**
    # backend/.env 里有效的 key，而内容护栏是 fail-closed ⇒ **所有内容保存失败**
    # （用户看到"内容含敏感信息未保存"）。故文本类调用不再绑定单一供应商。
    #
    # 文本类调用（内容护栏 / 查询改写 / 意图路由）走哪个供应商：
    #   dashscope（默认，qwen-flash，经 dashscope SDK）｜deepseek（OpenAI 兼容端点）。
    # ⚠️ **视觉调用（照片 caption）恒走 dashscope**——DeepSeek 无视觉模型，
    # 见 `external/dashscope.image_caption` 的注释。
    llm_provider: str = Field("dashscope", pattern=r"^(dashscope|deepseek)$")
    # DeepSeek（OpenAI 兼容）：别名同时收标准名与本机历史变量名 `deepseek-ugsimulator`
    # （2026-10-15：用户机上就是这个名字；用别名收编，免去改机器环境）。
    deepseek_api_key: str = Field(
        "", validation_alias=AliasChoices("DEEPSEEK_API_KEY", "deepseek-ugsimulator")
    )
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    # ── 微信绑定（2026-10-15 · 台账 §7.29）────────────────────────────────────
    # 授权登录（uni.login → code2session）成功后写入 `user_wechat_bindings` 用的**渠道**值：
    #   · wechat_app  —— 微信开放平台「移动应用」授权（决策 #8 的"APP 微信登录"）
    #   · miniprogram —— 小程序 wx.login（`/sns/jscode2session` 就是小程序端点）
    # ⚠️ 必须与 `db/models/wechat.WECHAT_BINDING_CHANNELS` 白名单一致（bind() 会校验）。
    wechat_login_channel: str = Field("wechat_app", pattern=r"^(wechat_app|miniprogram)$")
    # 企微「微信客服」Secret —— 用于 `cgi-bin/kf/customer/batchget` 把**客服侧 openid 解析成
    # unionid**（绑定的另一半：App 侧授权拿 unionid ⇄ 企微侧解析拿 unionid ⇄ 同一 user）。
    # 未配置 ⇒ 该解析**安全降级为不解析**（回调仍 200、消息仍记录，只是不归属）。
    wechat_kf_secret: str = ""

    # ── Agent 服务（AG5 接线；独立进程，见 agent/UPSTREAM.md）──
    # 本后端**反代**调用：客户端只认本后端（JWT 鉴权），user_id 只由本后端注入
    # （agent 服务自身不鉴权用户身份 —— 见 UPSTREAM 五-1，故绝不可暴露公网）。
    agent_service_base_url: str = "http://127.0.0.1:8300"
    # 与 agent 服务的共享密钥（对应其 AGENT_SERVICE_TOKEN）；留空则不带该头
    agent_service_token: str = ""

    # ── 本地自然日口径（功能修复波 D04-1 · 2026-09-25）──
    # 云侧"本地日"的**单一来源**（见 app/core/timeutil.py）：L1 日聚合与"去年今日"回响都用它。
    # **不读容器 TZ**（deploy 未设 TZ ⇒ 容器为 UTC；若沿用"服务器本地"则等于没修 D04-1）。
    # 值＝IANA 名；MVP 面向单城（沪）故默认 Asia/Shanghai；跨时区用户需 per-user 偏移（待拍板）。
    app_local_tz: str = "Asia/Shanghai"
    # 单轮对话超时（秒）：Agent 含多轮工具调用 + 推理，实测 76s 量级，故默认给足
    agent_service_timeout_s: float = 180.0
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
    # 媒体下发票据（Valet Key · 2026-08-31 新增）
    # 背景：<image :src> 带不了 Authorization header，私有图片无法用 Bearer 下发。
    # 解法：storage.get_download_url() 签发短时效签名 URL——COS 用原生 presigned，
    # fs/minio/fake 用 HMAC 票据指向 /api/v1/media/{key}（见 services/external/media_url.py）。
    # 签名密钥独立于 jwt_secret（泄漏面隔离）；留空时回退 jwt_secret。
    media_url_secret: str = ""
    # 双 TTL（对齐业界成熟做法）：缩略图长时效配合客户端缓存秒开；原图短时效防泄露。
    media_thumb_ttl: int = 86400        # 缩略图 24h
    media_original_ttl: int = 900       # 原图 15m
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
    #
    # D09-8（2026-09-25 功能修复波）：三项均补 `WECOM_*` 别名——`docs/拿key后推进计划.md`
    #   推荐注入的是 WECOM_CORP_ID/WECOM_TOKEN/WECOM_ENCODING_AES_KEY，而本文件原先只认
    #   WECHAT_*，且 SettingsConfigDict(extra="ignore") 会**静默丢弃**别名变量 ⇒ 按文档
    #   注入时 `_wechat_configured()` 恒 False、全部回调 503 且无任何告警。
    wechat_corp_id: str = Field(
        "", validation_alias=AliasChoices("WECHAT_CORP_ID", "WECOM_CORP_ID")
    )
    wechat_token: str = Field("", validation_alias=AliasChoices("WECHAT_TOKEN", "WECOM_TOKEN"))
    wechat_encoding_aes_key: str = Field(
        "", validation_alias=AliasChoices("WECHAT_ENCODING_AES_KEY", "WECOM_ENCODING_AES_KEY")
    )
    # D09-4（同日修复）：企微「回调 Token」（自定随机串）与「应用 Secret」（企微生成，
    #   用于 gettoken）**不是同一个值**。原实现把 `wechat_token` 直接当 corpsecret 用
    #   ⇒ 生产必然 40001 ⇒ 媒体链永久降级。这里独立成项；留空时仍回退 token
    #   （兼容既有单值部署，回退时打 WARNING 指明后果）。
    wechat_corp_secret: str = Field(
        "",
        validation_alias=AliasChoices(
            "WECHAT_CORP_SECRET", "WECOM_CORP_SECRET", "WECHAT_APP_SECRET"
        ),
    )
    # 微信开放平台 code2session 登录（决策 #8；Wave4-L 接入，替换 mock unionid）——
    # 未配置时 dev/test 走 mock、production 保持 501（不静默降级 mock 登录）。
    wechat_appid: str = ""
    wechat_secret: str = ""

    # 内测期设备码登录通道（2026-09-21 峰宝拍板：20 人内测零 UI 改动，按设备自助建档）。
    # ⚠️ fail-closed：**默认 False**——开启后 /api/v1/auth/device 是一条「任意 device_id
    #    自助注册」的入口，若默认放开等于永久留后门；关闭时返回 501 AUTH_014，
    #    语义与「微信未配置 → 501 AUTH_011」「短信未接入 → 501 AUTH_010」完全对齐。
    # 生命周期：内测期置 true → **上架前必须置回 false 并回收**（docs/决策台账.md §1.13）。
    allow_device_login: bool = False

    # 用户内容安全审核（B5b #8 · Wave4-L）：适配器开关
    #   tencent_ci = 当前顶替（文本=规则预检+护栏 / 图片=CI image_audit）
    #   aliyun     = 上架前启用（阿里云内容安全增强版，需 AccessKey + 开通「内容安全」服务）
    #   off        = 不调外部审核（文本仅本地规则、图片默认放行；调用方自行决定）
    content_safety_provider: Literal["tencent_ci", "aliyun", "off"] = "tencent_ci"
    # 生成态输出护栏（D10-8 · 2026-09-25 用户拍板「按建议来」）：
    # 四条用户可见生成链（照片 AI 描述 / 事件标题 / 画像开放值 / 对话回复）的审核档位——
    # 规则层（本地词表，零成本）**全量**生效；LLM 级只用于两处：
    #   ① 对话回复：每次一问一答 1 次（直接对用户可见，量小）；
    #   ② 照片描述：**按本比例确定性抽样**（哈希稳定，同一照片不会"这次审下次不审"）。
    # 0 = 照片描述不走 LLM（只规则层）；1 = 全量走（100 用户 × 30 张/天 ≈ 3000 次/天）。
    generated_guard_sample_rate: float = 0.1
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

    # 2026-10-19（搜索性能治理② · docs/性能验收_搜索_20261016.md）：BGE-M3 运行档位 settings 化。
    # 背景：encode 段 P95 1140ms（off 模式第三大头），首请求懒加载冷启动实测 12~14s；
    # 满核线程曾在 16GB 机器上 OOM，故线程数默认 4、不做"自动开满"。
    # device 留空 = sentence-transformers 自动选（有 CUDA 用 GPU，否则 CPU）。
    bge_device: str = ""
    bge_threads: int = 4
    # 启动期后台预热（默认关：开发机不必每次吃 1.2GB 常驻；部署/压测机建议开）。
    bge_warmup_on_start: bool = False

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

    # R-1 拒答地板（2026-10-09）：召回层稠密相似度低于此地板的命中整条剔除；
    # 若全部低于地板 → 搜索返回空结果（「答不出来就不答」）。
    #
    # 为什么需要：此前管线里**没有任何拒答路径** —— rerank_min_score=0.0 且 rerank
    # 默认关闭，LLM 精排又明确「绝不增删候选」，所以只要不触发 filter，非空查询必然
    # 返回 top-k，负样本 100% 误召回（门禁 negative_error_rate ≤0.05，实测 0.5714）。
    # 注意这是【召回层】地板，与 rerank_min_score（bge-reranker 重排层）不是一件事。
    #
    # ⚠️ 默认 0.0 = 关闭，即保持改动前的行为。`0.0` 与「地板为 0」等价：
    # 稠密分恒 ≥0，所以 0.0 不会剔除任何结果。
    # ⚠️ 标定现状：2026-10-09 用 50 条真值 + 12 条负样本实测 —— 在当前 117 条
    # **合成语料**上正负样本的 top1 稠密分几乎完全重叠（负 0.4888~0.6907，
    # 正 0.4399~1.0000），**任何阈值都无法在误召回 ≤0.05 的同时保住 hit_rate@3**。
    # 换成词汇覆盖、头部区分度等信号同样不可分（语料含大量占位文本）。
    # ⇒ 本项要真正达标，前置是 §2.1#3「扩真实语料」，不是再调代码。
    #   标定工具与完整权衡表：<仓库>/research/rag_benchmark/r1_calibrate.py
    retrieval_min_dense_score: float = 0.0

    # R-4 图片塔适配层（2026-10-14）：`image_vec`（以图搜图命名向量）用哪种模型出向量。
    #   bge_caption      = 现状：图片 → Qwen3-VL caption → BGE-M3 文本塔编码
    #                      （B2-4 允许的**替代路径**，等于用文本塔冒充图片塔）
    #   dashscope_vision = 设计指定方案：图片 → tongyi-embedding-vision-plus 直接出向量
    # ⚠️ 两者是**互不相通的向量空间**，余弦分不可比 —— 切换时必须先用
    #    `scripts/rebuild_image_index.py` 把已有图片点重灌，**再**改这里；
    #    检索端按 payload `image_space` 隔离（旧点无该键时按缺失兼容，不丢）。
    # ⚠️ `dashscope_vision` 依赖百炼「多模态向量」开通（当前未开通），
    #    未开通时该分支会抛错、由调用方降级（照片仍可被 text_vec 搜到）。
    image_tower_backend: Literal["bge_caption", "dashscope_vision"] = "bge_caption"
    # 视觉嵌入维度 —— **必须等于 vector_store.VECTOR_SIZE（1024）**，否则写入 Qdrant
    # 报维度不符。官方支持 64~1152（默认 1152），这里显式取 1024 以复用既有槽位。
    # 依据：https://help.aliyun.com/zh/model-studio/multimodal-embedding-api-reference
    image_tower_dimension: int = 1024

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


def agent_service_token_fingerprint(raw: str | None = None) -> str:
    """服务间令牌指纹（sha256 前 8 位十六进制）；空值返回 `"(空)"`。

    2026-10-16（台账 §7.12「静默降级显性化」）：backend 的 `agent_service_token`
    与 agent 的 `AGENT_SERVICE_TOKEN` **必须是同一个值**（同一把密钥、两个方向，
    见 `app/api/deps.py:agent_service_user`）。两侧不一致时 backend→agent 401、
    agent→backend 401 ⇒ agent 侧 W1 记忆镜像 / W2 检索合并**静默降级**，用户表现的
    形态是「AI 说已记住，App 时间轴却空白」，而两侧日志都只显示"已配置"、无从对账。

    指纹**只输出哈希前 8 位、不可逆**，绝不泄露令牌原文，仅用于两侧启动日志对账。

    算法**单一真源**在 `app.core.diagnostics.token_fingerprint`（其 docstring 声明
    与 `agent/src/yishu/security.py:token_fingerprint` 同算法——两侧改动必须同步）。
    """
    from app.core.diagnostics import token_fingerprint  # 延迟导入：避免 config⇄diagnostics 循环

    return token_fingerprint(settings.agent_service_token if raw is None else raw)


# 服务间令牌长度下限：指纹 sha256[:8] 被 /healthz 暴露时，其安全性完全依赖令牌不可猜
# （短/可猜令牌 ⇒ 指纹可被离线枚举比对，成为 oracle）。32 字符是按熵下限拍的经验值。
_TOKEN_MIN_LEN = 32


def assert_agent_service_token_config() -> bool:
    """启动期断言服务间令牌已配置并打印指纹（2026-10-16 · 静默降级显性化）。

    返回是否已配置，供调用方（lifespan）按需记录，但**本身不抛错**。

    ## 为什么需要

    `agent_service_token` 为空时，agent→backend 的记忆镜像（W1）与检索合并（W2）
    会**降级跳过**，用户侧的形态是「AI 说已记住，App 时间轴却空白」。启动日志里带上
    指纹，即可与 agent `/health` 的 `inbound_token_fp` 一眼对账（不同＝两侧密钥不一致）。

    ## 为什么刻意只告警、不抛错

    与 `_apply_production_safety` 里 JWT/refresh 的 fail-closed 断言**不同**：服务间
    通道是**增强**而非依赖——缺失时对话主链路照常可用。若在此 fail-closed，一次
    "令牌没配好"会升级成"整个后端起不来"，故障面更大。本项目取舍：
    **降级可以，隐形不行；但绝不把增强通道的配置缺失升级成全站不可用**。

    ## 长度下限（2026-10-16 追加 · 配合 /healthz 暴露指纹）

    `/healthz` 会暴露本令牌的指纹 `sha256[:8]`（供 `scripts/dev/status.ps1` 两端对账；
    该端点**为内网探活、不对外**——对账的价值场景正是生产）。此暴露的安全性**完全依赖
    令牌本身不可猜**：若令牌短/可猜，`sha256[:8]` 就成了**离线校验 oracle**（枚举候选
    → 比对指纹）。故对长度设下限（≥`_TOKEN_MIN_LEN`），不足时 **WARNING 但不抛错**
    （沿用同一口径——把服务打死比这个风险更糟）。
    """
    token = (settings.agent_service_token or "").strip()
    configured = bool(token)
    logger = logging.getLogger("yishu.config")
    logger.info(
        "[启动断言] 服务间令牌 agent_service_token=%s fp=%s"
        "（须与 agent /health 的 inbound_token_fp 相同；不同＝两侧密钥不一致）",
        "已配置" if configured else "未配置",
        agent_service_token_fingerprint(),
    )
    if not configured:
        logger.warning(
            "[启动断言] agent_service_token 未配置：agent→backend 的记忆镜像(W1)/"
            "检索合并(W2) 将降级跳过（用户表现＝「AI 说已记住，App 时间轴却空白」）。"
            "对话主链路不受影响。配置后重启，并比对本行 fp 与 agent 启动日志的 fp。"
        )
    elif len(token) < _TOKEN_MIN_LEN:
        logger.warning(
            "[启动断言] agent_service_token 过短（%d 字符 < %d）：/healthz 会暴露其指纹 "
            "sha256[:8]，短/可猜令牌可被**离线比对/枚举** ⇒ 指纹即 oracle。"
            "请换成随机长串（如 `openssl rand -hex 32`），两侧配同值后重启。",
            len(token),
            _TOKEN_MIN_LEN,
        )
    return configured
