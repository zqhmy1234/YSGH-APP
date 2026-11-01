"""客户端编译门的**日志判定**负向探针（2026-10-30 · 由一次真实的假通过沉淀）。

## 事故

P2 C-4/C-8 落地后跑 `python scripts/client_compile_gate.py`：退出码 **0**、输出
「编译产物已在本次调用后更新 ⇒ 判为成功」。但同一次调用的日志原文是：

```
23:33:58.636 [plugin:uts] Unexpected '/'. Escaping special characters with \\ may help.
23:33:58.643 at components/TabSearch/TabSearch.uvue:1:0
23:34:01.693 已停止运行...
```

即：**编译确实失败**（style 段注释少闭合一个 `*/`，裸露的中文 `色/字号` 触发解析错），
门禁却报通过。成因两步叠加：

1. `FAIL_HINTS` 只有「编译失败 / 语法错误 / SyntaxError / Unexpected token / 未找到 / Cannot find」
   —— 该错误**一个都不命中** ⇒ `bad=False`；
2. 失败后没有任何成功标记，于是掉进「产物时间戳旁证」分支，而 `manifest.json` **每次编译必被重写**
   ⇒ `touched` 恒真 ⇒ 判 ok。

## 锁住什么

`classify_log()` 的四个出口（need_open / fail / ok / undetermined），用**真实日志片段**做探针，
重点锁 ①事故原文必须判 fail；②任何失败特征都不许被产物旁证覆盖（该约束在 `compile_client` 里，
故同时做源码级防漂移）；③成功日志不被弱措辞误伤。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 门禁脚本目录（与仓库其它测试一致：从仓库根跑 pytest 时 `scripts` 可导入）
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "scripts"))

import client_compile_gate as gate  # noqa: E402

# 事故原文（逐字截取，勿"美化"——探针的价值就在措辞真实）
_INCIDENT_LOG = """\
23:32:53.045 项目 client 开始编译
23:33:56.331 当前工程15个页面，正在编译components/TabSearch/TabSearch...
23:33:58.636 [plugin:uts] Unexpected '/'. Escaping special characters with \\ may help.
23:33:58.643 at components/TabSearch/TabSearch.uvue:1:0
23:34:01.693 已停止运行...
"""

_OK_LOG = """\
23:32:53.045 项目 client 开始编译
23:33:56.331 当前工程15个页面，正在编译components/TabSearch/TabSearch...
23:34:02.100 编译成功
"""

_NEED_OPEN_LOG = "未检测到已打开的HBuilderX，请先执行cli open启动HBuilderX后再重试\n"


def test_incident_log_is_fail_not_ok() -> None:
    """事故原文必须判 fail —— 这是本文件的**核心断言**（旧实现判成了 ok）。"""
    verdict, reason = gate.classify_log(_INCIDENT_LOG)
    assert verdict == "fail", f"事故日志被判成 {verdict}（{reason}）——假通过回归"
    assert "结构性" in reason


def test_structural_forms_all_fail() -> None:
    """结构性错误的各种现实形态都要覆盖，不能只认某一种中文措辞。"""
    cases = {
        "[plugin:uvue] Something went wrong": "插件前缀",
        "Unexpected token }": "解析错",
        "Unexpected '/'": "解析错（斜杠）",
        "已停止运行...": "编译中断",
        "ERROR in ./pages/x.uvue": "打包器报错",
        "SyntaxError: Unexpected end of input": "语法错",
        "Cannot find module '@/utils/nope'": "缺模块",
        "at utils/highlight.uts:88": "只有定位行",
    }
    for text, why in cases.items():
        verdict, _ = gate.classify_log(text)
        assert verdict == "fail", f"{why} 未被判 fail：{text!r} → {verdict}"


def test_ok_log_not_misjudged() -> None:
    """成功日志必须判 ok；含弱措辞告警的成功日志也不许被误伤。"""
    assert gate.classify_log(_OK_LOG)[0] == "ok"
    noisy_ok = _OK_LOG + "未找到可选插件 xxx，已跳过\n"
    assert gate.classify_log(noisy_ok)[0] == "ok", "成功终态不该被弱措辞告警覆盖"


def test_real_success_log_is_ok() -> None:
    """真·成功日志（2026-10-30 18:41 实跑原文）必须判 ok。

    这份日志同时含 `[plugin:uni:app-uvue-css] WARNING: ...`（CSS 告警）、
    `at components/TabIndex/TabIndex.uvue:63:3`（告警定位行）与收尾 `已停止运行...`
    —— 三条全都是**成功构建也会出现**的。加固后的门禁第一次复跑就因此假红，
    所以这里用原文钉死：成功标记优先，告警措辞与定位行不得反向覆盖。
    """
    real_ok = """\
23:40:49.316 [plugin:uni:app-uvue-css] WARNING: Unsupported dynamic style property 'background'.
23:40:49.326 at components/TabIndex/TabIndex.uvue:63:3
23:41:03.099 项目 client 编译成功。
23:41:03.121 ready in 98835ms.
23:41:03.172 已停止运行...
"""
    verdict, reason = gate.classify_log(real_ok)
    assert verdict == "ok", f"真实成功日志被判成 {verdict}（{reason}）—— 假红回归"


def test_need_open_is_not_pass() -> None:
    """HBuilderX 没开时 cli 不编译 —— 必须单独识别，绝不能落进产物旁证。"""
    assert gate.classify_log(_NEED_OPEN_LOG)[0] == "need_open"


def test_empty_log_is_undetermined() -> None:
    """空日志 / 纯进度日志**不能**自带结论（是否 ok 由产物旁证决定，且在调用方）。"""
    assert gate.classify_log("")[0] == "undetermined"
    assert gate.classify_log("23:33:56.484 当前工程15个页面，正在编译App...\n")[0] == "undetermined"


def test_artifact_fallback_only_for_undetermined() -> None:
    """源码级防漂移：产物旁证只能兜 `undetermined`，失败特征不得被时间戳覆盖。"""
    src = (_ROOT / "scripts" / "client_compile_gate.py").read_text(encoding="utf-8")
    assert 'verdict == "undetermined" and touched' in src, (
        "产物旁证分支必须带 `verdict == \"undetermined\"` 前置条件——"
        "否则任何未识别的失败都会被 manifest.json 的 mtime 判成通过（2026-10-30 事故成因 2）"
    )
    # `bad` 变量已由 classify_log 取代；若它复活说明判定又分叉成两处。
    assert "bad = any(" not in src, "判定只准有 classify_log 一个出口，禁止再起一套 bad 标记"
