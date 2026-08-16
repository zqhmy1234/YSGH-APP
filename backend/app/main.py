"""忆述光华 backend · FastAPI 入口

开发决策 #3：FastAPI + RQ/Redis + PostgreSQL
- /api/v1/* 业务路由（OpenAPI 契约即代码：/docs 自动生成）
- Mock 模式：MOCK_EXTERNAL_AI=true 时外部 AI 零费用，契约消费方可联调
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, contents, events, search
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

app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO(T1): 生产收紧为白名单
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handlers(app)

app.include_router(auth.router)
app.include_router(contents.router)
app.include_router(events.router)
app.include_router(search.router)


@app.get("/healthz", tags=["meta"])
def healthz():
    """健康检查（CI/部署探针）"""
    return {"status": "ok", "env": settings.app_env, "mock_external_ai": settings.mock_external_ai}
