"""pytest 根配置（P3 顺手清理：消除 23 个测试文件的 sys.path 样板）

conftest.py 所在目录（backend/）会被 pytest 自动加入 sys.path，
因此 tests/ 下的测试可直接 `import app.*`，无需手写
`sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`。
存量测试的样板保留（无害），新测试不再需要。
"""
import pytest


@pytest.fixture(autouse=True)
def _force_fake_storage(monkeypatch):
    """测试隔离：强制 storage_backend=fake + 重置 fake 单例（2026-08-26）

    背景：本地 .env 可配 STORAGE_BACKEND=fs（开发真实文件存储），且存在跨模块
    顺序依赖导致 get_storage_backend() 偶发返回 fs 实例（test_upload ×
    test_content_upload 并跑时 test_upload_photo_success 断言失败）。
    测试套件统一按 fake 断言（P2-04 reset_storage_backend 原意），此处 autouse 固化。
    """
    from app.core.config import settings
    from app.services.external.storage import reset_storage_backend

    monkeypatch.setattr(settings, "storage_backend", "fake")
    reset_storage_backend()
