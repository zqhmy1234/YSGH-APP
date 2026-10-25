"""B-03 · agent 侧「敏感话题」能力的明确降级文案测试（2026-10-26）

## 为什么是「降级文案」而不是「接线」

用户要求过「AI 能帮我改敏感话题设置」。但**通道上做不到**：

- 后端 `backend/app/api/contents/profile_sensitive.py` 的
  `GET/POST/DELETE /api/v1/contents/profile/sensitive` 三个端点都依赖
  `get_current_user`（**用户 JWT Bearer**）；
- agent 进程只持有 `X-Agent-Token`（服务间令牌），**不持有用户身份**
  ⇒ 实测调用返回 **401**。

要让 agent 真能改，只能给服务间令牌开一个"能以任意用户身份写敏感话题"的通道——
那等于**给服务密钥开了冒充任意用户的权限**，是明确不能做的事（安全面远大于功能价值）。

总账给的处置是二选一：「接线**或**明确降级并改文案（不可沉默）」。本批选后者。

## 本测试钉住什么

1. system prompt（`agent/config/agent_llm_config.json` 的 `sp`，是 prompt 的**唯一真源**）
   确实含有降级说明 —— 否则 LLM 会继续"假装做了"；
2. 文案里必须包含**可执行的指路**，且路径与客户端真实导航一致
   （写错路径等于让用户白找，比不写更糟）；
3. 必须显式禁止「已经帮你设好了」这类**假承诺**说法；
4. 反向保证：prompt 里**不得**再声称 agent 具备该能力（避免文案自相矛盾）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
CONFIG = REPO / "agent" / "config" / "agent_llm_config.json"

# 客户端真实导航（实测自 client/components/TabProfile/TabProfile.uvue:235
# 与 client/pages.json:37-39 —— 「我的」里那一行显示「画像设置」，页面标题「画像管理」）
NAV_ROUTE = "pages/portrait/manage"


@pytest.fixture(scope="module")
def sp() -> str:
    """system prompt 原文（唯一真源）"""
    assert CONFIG.exists(), f"prompt 配置文件缺失：{CONFIG}"
    return json.loads(CONFIG.read_text(encoding="utf-8"))["sp"]


class TestSensitiveTopicDegradation:
    def test_prompt_has_degradation_section(self, sp):
        """prompt 必须含「敏感话题」降级段（否则 LLM 会假装能做）"""
        assert "敏感话题" in sp, "system prompt 缺少敏感话题相关说明"
        assert "改不了" in sp or "做不到" in sp, (
            "必须明确说出『做不到』——不能含糊成『我尽量』"
        )

    def test_prompt_states_why_it_cannot(self, sp):
        """必须说明**原因**（无用户身份凭证），而不是只说「不支持」

        只说"不支持"会让用户以为是产品懒；说清"需要你本人登录身份"用户才理解
        这不是缺陷而是安全边界。
        """
        assert "身份凭据" in sp or "身份凭证" in sp, "应说明缺少用户身份凭证"
        assert "服务间令牌" in sp, "应说明 agent 只有服务间令牌"

    def test_prompt_gives_real_navigation_path(self, sp):
        """指路路径必须与客户端真实导航一致（写错路径比不写更糟）"""
        assert "画像设置" in sp, "应指向客户端真实入口「画像设置」"
        assert "敏感话题" in sp
        assert NAV_ROUTE in sp, f"应写明真实路由 {NAV_ROUTE}（可核对 pages.json）"

    def test_prompt_forbids_fake_completion(self, sp):
        """必须显式禁止「已经帮你设好了」这类假承诺"""
        assert "禁止声称" in sp or "禁止" in sp
        # 关键：不得出现把"做不到"包装成"已完成"的话术
        for bad in ("已经帮你设好了", "已经帮你设置好了", "已为你设置"):
            # 这些短语**只能以「禁止…」的形式出现**，不能作为指令
            if bad in sp:
                idx = sp.find(bad)
                window = sp[max(0, idx - 30) : idx + len(bad)]
                assert "禁止" in window, (
                    f"「{bad}」出现在 prompt 中但不是禁止语境（会被 LLM 当成可执行指令）：{window!r}"
                )

    def test_prompt_does_not_claim_capability(self, sp):
        """反向保证：不得声称 agent 具备修改敏感话题的能力

        与降级文案自相矛盾的话，LLM 会挑对自己"更有利"的那条执行。
        """
        for claim in ("帮你设置敏感话题", "帮你添加敏感话题", "管理敏感话题设置"):
            assert claim not in sp, f"prompt 仍在宣传不具备的能力：{claim}"

    def test_prompt_forbids_echoing_sensitive_content(self, sp):
        """用户说「别再提某事」后，不得把该内容复述进回复"""
        assert "复述" in sp, "应禁止复述用户的敏感话题内容"

    def test_prompt_still_has_core_sections(self, sp):
        """改动不得破坏既有 prompt（防"改一段把别的段落删了"）"""
        for anchor in ("# 角色定义", "# 核心能力", "# 约束", "语言限制"):
            assert anchor in sp, f"既有段落被误删：{anchor}"
        # V3 标签体系是 prompt 主体，必须还在
        assert "L1-A" in sp and "L3" in sp, "V3 标签体系段落被破坏"


class TestConfigIntegrity:
    def test_config_json_still_valid_and_complete(self):
        """编辑后 JSON 仍合法，且结构与工具清单未被破坏"""
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        assert set(cfg.keys()) >= {"config", "sp", "tools"}
        assert isinstance(cfg["tools"], list) and len(cfg["tools"]) >= 13, (
            "工具清单被意外改动（B-03 只应改 prompt，不应增删工具）"
        )
        assert cfg["config"]["model"], "模型名被清空"

    def test_no_profile_tools_claimed(self, sp):
        """明确：本批**不**加 profile 工具，prompt 也就不得宣称有

        若将来真要接（需用户 JWT 透传方案），必须同时改 `_TOOL_LIST()` 与本测试。
        """
        assert "profile_sensitive" not in sp
        assert "add_sensitive_topic" not in sp
        assert "list_sensitive_topics" not in sp
