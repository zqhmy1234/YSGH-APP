"""忆述光华 backend · FastAPI 入口

开发决策 #3：FastAPI + RQ/Redis + PostgreSQL
- /api/v1/* 业务路由（OpenAPI 契约即代码：/docs 自动生成）
- Mock 模式：MOCK_EXTERNAL_AI=true 时外部 AI 零费用，契约消费方可联调

G2/R6#11 加固（2026-08-27）：
- 安全响应头：X-Content-Type-Options / X-Frame-Options / Referrer-Policy，
  HSTS 仅生产（app_env=production）下发（防点击劫持/MIME 嗅探/信息泄漏）
- 生产关闭文档暴露：app_env=production 时 docs_url/openapi_url/redoc_url 全置 None
- G2/R6#15：/healthz 只暴露最小存活信息 {status: ok}（不泄露 env/mock/DB/版本明细）
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import (  # noqa: E501
    asr,
    auth,
    classify,
    contents,
    corrections,
    echo,
    events,
    interview,
    messages,
    search,
    sync,
    upload,
    wechat,
)
from app.api.contents import profile_sensitive_router
from app.api.event_items import router as event_items_router
from app.api.thumbnails import router as thumbnails_router  # B4 缩略图（Wave3 AgentG 提供，集成接线）
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.middleware import RequestIDMiddleware
from app.core.ratelimit import RateLimitMiddleware  # G1/R6#2/#3：通用限流（auth/ASR/搜索）


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：Sentry 初始化（决策 #12，生产环境）
    if settings.sentry_dsn and settings.app_env == "production":
        import sentry_sdk
        sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)
    yield


def _is_production() -> bool:
    return settings.app_env == "production"


def create_app() -> FastAPI:
    """构建应用（G2/R6#11：docs_url 生产置 None——生产关闭 OpenAPI 文档暴露）。

    拆出可测函数：单测以 monkeypatch app_env 后调用 create_app() 验证
    生产分支（/docs、/openapi.json、/redoc 全 404 + HSTS 头）。
    """
    prod = _is_production()
    app = FastAPI(
        title="忆述光华 API",
        version="0.1.0",
        description=(
            "个人记忆整理与回顾后端。功能：认证（微信/手机号/JWT）、内容入库"
            "（照片/文字/语音）、四层事件聚合、描述性搜索（RAG）、同步（LWW）、"
            "AI 分类/画像/护栏。契约对齐《忆述光华_Sprint1规划.md》S1-02。"
        ),
        lifespan=lifespan,
        docs_url=None if prod else "/docs",
        openapi_url=None if prod else "/openapi.json",
        redoc_url=None if prod else "/redoc",
    )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        """G2/R6#11：安全响应头（纵深——错误响应同样带，防 MIME 嗅探/点击劫持）"""
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if _is_production():
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    def _cors_origins() -> list[str]:
        """CORS 白名单（审查修复 P1-05）：配置了 cors_origins 用配置；
        生产默认同源（空）；开发/测试默认放开（原 allow_origins=["*"] 仅限非生产）。"""
        if settings.cors_origins:
            return [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
        if _is_production():
            return []
        return ["*"]

    # 中间件顺序（Starlette 后加者最外层）：
    #   CORS(最外) → RequestID → RateLimit(最内)。RateLimit 置于 RequestID 内侧，
    #   G1/R6#3：限流 429 响应同样带 X-Request-ID，不破坏 API-008 全链路日志串联。
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    install_error_handlers(app)

    app.include_router(auth.router)
    app.include_router(contents.router)
    app.include_router(profile_sensitive_router)  # B5b FIX-4：画像级敏感增删查（Wave1 AgentC 提供，集成接线）
    app.include_router(event_items_router)  # B3-4 照片→事件反向入口（Wave2 AgentE 提供）
    app.include_router(thumbnails_router)  # B4 缩略图 GET /api/v1/thumbnails/{content_id}（Wave3 AgentG 提供）
    app.include_router(events.router)
    app.include_router(search.router)
    app.include_router(classify.router)
    app.include_router(corrections.router)
    app.include_router(asr.router)
    app.include_router(asr.guard_router)
    app.include_router(sync.router)
    app.include_router(echo.router)
    app.include_router(interview.router)
    app.include_router(wechat.router)
    app.include_router(messages.router)
    app.include_router(upload.router)

    @app.get("/healthz", tags=["meta"])
    def healthz():
        """健康检查（CI/部署探针）——G2/R6#15 收敛：只暴露最小存活信息，
        不泄露 env/mock 开关/DB 连接串/版本明细（避免探测信息辅助攻击面测绘）。"""
        return {"status": "ok"}

    return app


app = create_app()
