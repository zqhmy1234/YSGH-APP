"""忆述光华 backend · 应用配置（pydantic-settings）"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """统一配置中心，读取 backend/.env（参考 .env.example）"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 环境
    app_env: str = "development"
    debug: bool = True

    # 服务
    host: str = "0.0.0.0"
    port: int = 8000
    base_url: str = "http://localhost:8000"

    # 数据库
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/yishu"

    # Redis / RQ
    redis_url: str = "redis://localhost:6379/0"

    # JWT（决策 #8）
    jwt_secret: str = "change-me-in-production"
    jwt_access_ttl_minutes: int = 120
    jwt_refresh_ttl_days: int = 30
    jwt_algorithm: str = "HS256"

    # 外部 API Key
    dashscope_api_key: str = ""
    tencent_secret_id: str = ""
    tencent_secret_key: str = ""
    cos_bucket: str = ""
    cos_region: str = ""
    baidu_api_key: str = ""
    baidu_secret_key: str = ""
    amap_api_key: str = ""

    # 可观测
    sentry_dsn: str = ""

    # Mock 开关：true 时外部 AI 全部走本地 mock（零费用，契约消费方联调用）
    mock_external_ai: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
