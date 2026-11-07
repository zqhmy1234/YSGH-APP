"""pytest 根配置（P3 顺手清理：消除 23 个测试文件的 sys.path 样板）

conftest.py 所在目录（backend/）会被 pytest 自动加入 sys.path，
因此 tests/ 下的测试可直接 `import app.*`，无需手写
`sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`。
存量测试的样板保留（无害），新测试不再需要。
"""
import pytest


@pytest.fixture(autouse=True)
def _force_test_env(monkeypatch):
    """测试隔离：强制 mock 外部 AI + storage_backend=fake + 重置 fake 单例（2026-08-26）

    背景：本地 .env 可配 MOCK_EXTERNAL_AI=false / STORAGE_BACKEND=fs（开发真实通道），
    手动跑 pytest 若不覆盖会真实调用百炼/腾讯云（烧 key 额度 + 慢）；
    test_agent.py 虽在 run_pytest 里覆盖了 env，但手动 pytest 仍会走真实通道——
    此处 autouse 固化（2026-08-26 系统性审查修复），与 test_agent 双保险。
    真实验证请走独立流程（scripts/api_smoke_cases.py 显式设 MOCK_EXTERNAL_AI=false）。
    """
    from app.core.config import settings
    from app.services.external.storage import reset_storage_backend

    monkeypatch.setattr(settings, "mock_external_ai", True)
    monkeypatch.setattr(settings, "storage_backend", "fake")
    reset_storage_backend()
