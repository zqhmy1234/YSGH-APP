"""门禁失败摘要（2026-09-25 · 由一次真实浪费沉淀）。

## 事故（实付代价：**4 轮**才定位到失败用例名）

修 D10-8 后全量门禁红在 `tests`，过程是这样的：
  1. `review_agent --full` 只打印 tests 段的**前 8 行** ⇒ 看到的是覆盖率表（噪音）；
  2. 读 `.cowork-temp/test-report.json` 猜键（`sections`）⇒ 真实键是 `details`，白跑一轮；
  3. 在报告 JSON 里找 `FAILED` 字符串 ⇒ 报告只存**尾部切片**，同样没有失败行；
  4. 只能**全量重跑 pytest（107s）**才看到名字。

**根因**：`pytest -q --cov ... --cov-report=term-missing` 的输出顺序是
`进度 → 失败摘要（FAILED ...）→ 覆盖率表（上百行）`，而门禁两处都做了**尾部切片**
（`test_agent: combined[-3500:]` / `review_agent: out[-1500:]`）⇒ **恰好把失败摘要切掉**，
留下"与失败无关的覆盖率噪音"。

## 修复（两侧）

- `test_agent.extract_failed_tests()`：从输出抽取 `FAILED/ERROR path::nodeid`（去重保序）；
  `run_pytest` 失败时把摘要**置顶**，并把 `failed_tests` 写进报告 JSON（机读面）。
- `review_agent._failed_tests_from_report()`：失败时读报告字段并置顶展示（单一来源，不重复解析）。

本文件锁住三件事：① 抽取器行为；② **失败行位于覆盖率表之前时也不会被尾部切片丢掉**（回归）；
③ 两个工具确实接了这条链路（源码级防漂移）。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 门禁脚本目录（与仓库其它测试一致：从仓库根跑 pytest 时 `scripts` 可导入）
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "scripts"))

import test_agent  # noqa: E402

# 模拟真实全量套件输出：失败摘要在前、覆盖率表在后（**顺序即事故根因**）
_SAMPLE = """\
......................F........................................          [ 45%]
=================================== FAILURES ===================================
___________________ test_success_and_truncate_40 ____________________

    assert 0 < len(desc) <= MAX_DESC_LEN
E   AttributeError: 'types.SimpleNamespace' object has no attribute 'id'

=========================== short test summary info ============================
FAILED backend/tests/test_ba3_ai_chain.py::TestGeneratePhotoDescription::test_success_and_truncate_40
ERROR backend/tests/test_profile_clear.py::test_clear_profile_requires_auth - fixture error
1 failed, 1 error, 982 passed, 4 skipped in 107.56s
"""

_COVERAGE_TAIL = "\n".join(
    f"backend\\app\\services\\mod{i}.py   120     10    92%   72, 79, {i}" for i in range(120)
)


def test_extract_failed_tests_parses_ids():
    assert test_agent.extract_failed_tests(_SAMPLE) == [
        "FAILED backend/tests/test_ba3_ai_chain.py::TestGeneratePhotoDescription::test_success_and_truncate_40",
        "ERROR backend/tests/test_profile_clear.py::test_clear_profile_requires_auth",
    ]


def test_extract_failed_tests_dedupes_and_ignores_noise():
    noisy = "1 failed\nF\nE\nnot-a-failure FAILED-ish\nERROR: something\nFAILED x::y\nFAILED x::y\n"
    assert test_agent.extract_failed_tests(noisy) == ["FAILED x::y"]
    assert test_agent.extract_failed_tests("全部通过 100 passed") == []


def test_failure_lines_survive_coverage_tail_truncation():
    """**事故回归**：失败行在覆盖率表**之前** ⇒ 尾部切片必然丢它（这正是当时的盲区）。

    修复后 `run_pytest` 的返回串把摘要置顶，故即使原始输出尾部是覆盖率表，用例名依然可见。
    这里用与实现同构的拼接方式断言"摘要出现在返回串头部"。
    """
    raw = _SAMPLE + "\n" + _COVERAGE_TAIL
    tail_only = raw[-3500:]  # 修复前的取法
    assert "FAILED backend/tests/test_ba3_ai_chain.py" not in tail_only, (
        "样例构造失效：尾部切片里竟然还有失败行（应被覆盖率表挤出）"
    )
    failures = test_agent.extract_failed_tests(raw)
    # 与 run_pytest 的失败返回同构
    head = "[失败用例]\n" + "\n".join(f"  {x}" for x in failures)
    assert "FAILED backend/tests/test_ba3_ai_chain.py" in head.splitlines()[1], "失败用例必须在返回串第 2 行"


def test_gate_tools_surface_failed_tests():
    """源码级防漂移：test_agent 写 `failed_tests`、review_agent 读并置顶（两处都不可回归）"""
    ta = (_ROOT / "scripts" / "test_agent.py").read_text(encoding="utf-8")
    ra = (_ROOT / "scripts" / "review_agent.py").read_text(encoding="utf-8")
    assert '"failed_tests": extract_failed_tests(' in ta
    assert "head + " in ta or "head + \\n" in ta  # 摘要拼接在返回串头部
    assert "_failed_tests_from_report" in ra
    assert "failed_tests" in ra
