"""llm_ops/moderate.py 策略选择器测试（重构批次 A1 · P0-1 解环，2026-08-27）

覆盖：
- 托管优先：qwen_response_check 可用 → 直接返回托管判定（不触达 chat 兜底）
- chat 兜底：qwen_response_check 抛 RuntimeError → dashscope.moderate 结果
- 行为等价迁移：mock 模式下 base.moderate / guard_managed.moderate_managed /
  moderate.moderate 三个入口判定一致（托管不可用 → 同一 chat/mock 契约）
- 解环验证：llm_ops 各模块任意导入顺序冷启动无 ImportError（模块环已拆）
"""
from __future__ import annotations

import importlib
import sys

# 注意：不能用 `import app.services.llm_ops.moderate as moderate_mod`——
# llm_ops/__init__.py 会把包属性 moderate 覆盖为 base.moderate 函数（同名遮蔽），
# `import a.b.c as x` 按属性链取末段会拿到函数而非模块；这里经 sys.modules
# 取真模块对象以便 monkeypatch 其内部符号。
moderate_mod = importlib.import_module("app.services.llm_ops.moderate")


def test_selector_managed_first(monkeypatch):
    """托管可用 → 托管判定直接返回（不触发 chat 兜底）"""
    managed_verdict = {
        "pass": False,
        "reason": "managed-block",
        "detector": "managed",
        "detail": "BLOCK",
    }
    chat_calls = {"n": 0}

    def _fake_managed(text):
        return managed_verdict

    def _fake_chat(text):
        chat_calls["n"] += 1
        return {"pass": True, "reason": "mock"}

    monkeypatch.setattr(moderate_mod, "qwen_response_check", _fake_managed)
    monkeypatch.setattr(moderate_mod.dashscope, "moderate", _fake_chat)
    result = moderate_mod.moderate("违规内容")
    assert result == managed_verdict
    assert chat_calls["n"] == 0


def test_selector_chat_fallback(monkeypatch):
    """托管不可用（RuntimeError）→ chat 兜底（dashscope.moderate）"""
    chat_verdict = {"pass": False, "reason": "guard", "action": "reject"}
    chat_calls = {"n": 0, "text": None}

    def _fake_managed(text):
        raise RuntimeError("百炼未配置，托管护栏不可用")

    def _fake_chat(text):
        chat_calls["n"] += 1
        chat_calls["text"] = text
        return chat_verdict

    monkeypatch.setattr(moderate_mod, "qwen_response_check", _fake_managed)
    monkeypatch.setattr(moderate_mod.dashscope, "moderate", _fake_chat)
    result = moderate_mod.moderate("测试内容")
    assert result == chat_verdict
    assert chat_calls["n"] == 1
    assert chat_calls["text"] == "测试内容"


def test_mock_mode_entries_consistent():
    """mock 模式（测试环境强制 MOCK_EXTERNAL_AI=true）：三入口判定一致

    base.moderate / guard_managed.moderate_managed / moderate.moderate
    在托管不可用下均落到 dashscope.moderate 的 mock 契约（规则命中拦截、否则放行）。
    """
    from app.core.config import settings

    assert settings.mock_external_ai is True, "测试环境要求 MOCK_EXTERNAL_AI=true"

    from app.services.llm_ops.base import moderate as base_moderate
    from app.services.llm_ops.guard_managed import moderate_managed

    entries = (moderate_mod.moderate, base_moderate, moderate_managed)
    # 硬规则命中 → 三个入口全部拦截
    for entry in entries:
        verdict = entry("出售枪支的联系方式")
        assert verdict["pass"] is False, entry.__name__
    # 常规文本 → 三个入口全部放行
    for entry in entries:
        verdict = entry("今天天气不错")
        assert verdict["pass"] is True, entry.__name__


def test_moderate_selector_reexport_uniform():
    """base.moderate 与选择器为同一实现来源：mock 下返回结构兼容（含 reason 键）"""
    from app.services.llm_ops.base import moderate as base_moderate

    verdict = base_moderate("今天天气不错")
    assert isinstance(verdict, dict)
    assert "pass" in verdict and "reason" in verdict
    assert verdict["pass"] is True


def test_no_import_cycle():
    """解环：任意导入顺序下 llm_ops 包均可冷启动（无 ImportError）

    放在文件末尾：会重载模块（sys.modules 弹出后重建），避免影响
    前面用例对 moderate_mod 模块对象的引用。
    """
    mods = (
        "app.services.llm_ops",
        "app.services.llm_ops.base",
        "app.services.llm_ops.moderate",
        "app.services.llm_ops.guard_managed",
    )
    # 先按「base → moderate → guard_managed」顺序冷加载
    for mod in mods:
        sys.modules.pop(mod, None)
    importlib.import_module("app.services.llm_ops.base")
    importlib.import_module("app.services.llm_ops.moderate")
    importlib.import_module("app.services.llm_ops.guard_managed")
    # 再按「guard_managed → moderate → base」逆序冷加载
    for mod in mods:
        sys.modules.pop(mod, None)
    importlib.import_module("app.services.llm_ops.guard_managed")
    importlib.import_module("app.services.llm_ops.moderate")
    importlib.import_module("app.services.llm_ops.base")
