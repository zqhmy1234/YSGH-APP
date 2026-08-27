"""错误码登记表专项测试（H3 · 由 test_techdebt_p0.py 按域拆分而来，2026-08-27）

覆盖（原 P0-7）：
  - 登记表码唯一（同码多义拆分的根因约束）
  - http 语义匹配：retryable 仅限 5xx；4xx 不可重试；消息非空
  - raise 处码全部在表内（AST 扫描，防新码漏登记/撞号）
  - 已知拆分回归：CONTENT_003/008/007/EVENT_005
"""
import ast
from pathlib import Path

import pytest

# H3/R8#10：AST 扫描 + 登记表语义纯单测（无外部依赖）→ unit 分层
pytestmark = pytest.mark.unit

BACKEND_APP = Path(__file__).resolve().parent.parent / "app"


def _raise_site_codes() -> set[str]:
    """AST 扫描 backend/app 全部 `ApiError("<CODE>"` 字面量（raise 处实际使用的码）"""
    codes: set[str] = set()
    for py in BACKEND_APP.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ApiError"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                codes.add(node.args[0].value)
    return codes


def test_error_registry_codes_unique():
    """P0-7：登记表码唯一（同码多义拆分的根因约束）"""
    from app.core.errors import ERROR_REGISTRY

    specs = list(ERROR_REGISTRY.values())
    assert len({s.code for s in specs}) == len(specs)


def test_error_registry_http_semantics():
    """P0-7：http 语义匹配——retryable 仅限 5xx；4xx 不可重试；消息非空"""
    from app.core.errors import ERROR_REGISTRY

    for spec in ERROR_REGISTRY.values():
        assert spec.message, f"{spec.code} 缺少语义描述"
        if spec.retryable:
            assert spec.http >= 500, f"{spec.code} retryable=True 但 http={spec.http}（仅 5xx 可重试）"
        if 400 <= spec.http < 500:
            assert not spec.retryable, f"{spec.code} 4xx 不应标记 retryable"


def test_error_registry_covers_all_raise_sites():
    """P0-7：全仓 raise 处使用的码必须已登记（防新码漏登记/撞号）"""
    from app.core.errors import ERROR_REGISTRY

    used = _raise_site_codes()
    missing = used - set(ERROR_REGISTRY)
    assert not missing, f"raise 处存在未登记错误码: {sorted(missing)}"


def test_error_registry_known_splits():
    """P0-7 拆分回归：CONTENT_003 仅敏感语义、CONTENT_008 游标、EVENT_005 内容不存在"""
    from app.core.errors import ERROR_REGISTRY

    assert ERROR_REGISTRY["CONTENT_003"].http == 422
    assert ERROR_REGISTRY["CONTENT_008"].http == 422
    assert ERROR_REGISTRY["EVENT_005"].http == 404
    assert ERROR_REGISTRY["CONTENT_007"].http == 413  # 413 语义不再被 404 污染
