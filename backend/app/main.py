"""忆述光华 backend · FastAPI 入口

开发决策 #3：FastAPI + RQ/Redis + PostgreSQL
- /api/v1/* 业务路由（OpenAPI 契约即代码：/docs 自动生成）
- Mock 模式：MOCK_EXTERNAL_AI=true 时外部 AI 零费用，契约消费方可联调
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
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
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.middleware import RequestIDMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：Sentry 初始化（决策 #12，生产环境）
    if settings.sentry_dsn and settings.app_env == "production":
        import sentry_sdk
        sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)
    yield


app = FastAPI(
    title="忆述光华 API",
    version="0.1.0",
    description=(
        "个人记忆整理与回顾后端。功能：认证（微信/手机号/JWT）、内容入库"
        "（照片/文字/语音）、四层事件聚合、描述性搜索（RAG）、同步（LWW）、"
        "AI 分类/画像/护栏。契约对齐《忆述光华_Sprint1规划.md》S1-02。"
    ),
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

def _cors_origins() -> list[str]:
    """CORS 白名单（审查修复 P1-05）：配置了 cors_origins 用配置；
    生产默认同源（空）；开发/测试默认放开（原 allow_origins=["*"] 仅限非生产）。"""
    if settings.cors_origins:
        return [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if settings.app_env == "production":
        return []
    return ["*"]


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
    """健康检查（CI/部署探针）"""
    return {"status": "ok", "env": settings.app_env, "mock_external_ai": settings.mock_external_ai}
