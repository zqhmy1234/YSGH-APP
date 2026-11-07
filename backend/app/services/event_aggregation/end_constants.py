"""端侧聚合常量读取与端云比对（AGG-016 契约 · D04-14 修复 2026-09-25）。

## 为什么需要

`run_validation.py` 原有的两条 AGG-016 断言都是**假校验**：

1. `aggregate(photos)` 与 `aggregate(photos)` 对比 —— **自己跟自己比**（确定性函数 ⇒ 恒真）；
2. `AGG_CONFIG["l0"]["eps_s_m"] == 500.0` —— **常量与字面量自比**（恒真）。

它们既发现不了端云漂移，也发现不了有人把云侧常量改错。本模块读**端侧真源**
`client/utils/agg/agg_config.uts`（`export const L0_EPS_S_M: number = 500.0` 形式），
把云侧常量逐项与之比对 —— 这才叫"端云同一配置源"。

## 用法与边界

⚠️ 生产镜像可能不含 `client/`（同类问题见审计 D07-1：镜像无 `docs/`）⇒ `read_end_constants()`
返回 `None`，调用方必须**显式**记录"本次未比对"，**不得**把 None 当作通过。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.services.event_aggregation import agg_types

REPO_ROOT = Path(__file__).resolve().parents[4]
END_CONFIG_REL = Path("client") / "utils" / "agg" / "agg_config.uts"

# 取值表达式：**截到行尾**再手工去注释。⚠️ 别把 `/` 放进字符类——端侧每行行尾注释是 `//`，
# 贪婪匹配会把注释符吞进表达式（实测：`3600.0       /` → float 解析失败 ⇒ 解析出 0 项、
# 门禁自己"空转"却报绿）。本文件顶部的"防空转"断言（`len(end) >= 6`）正是为此加的。
_END_CONST_RE = re.compile(r"export\s+const\s+([A-Z0-9_]+)\s*:\s*number\s*=\s*([^\n]*)")

# 云侧常量名 → 端侧常量名（名字不同、语义必须相同）
CLOUD_TO_END = {
    "L0_EPS_T_SEC": "L0_EPS_T_SEC_DEFAULT",          # 云侧无 _DEFAULT 后缀，端侧有
    "L0_EPS_T_SEC_CONSERVATIVE": "L0_EPS_T_SEC_CONSERVATIVE",
    "L0_EPS_S_M": "L0_EPS_S_M",
    "L0_MIN_PTS": "L0_MIN_PTS",
    "BURST_GAP_SEC": "BURST_GAP_SEC",
    "WALK_SPEED_MS": "WALK_SPEED_MS",
    "DRIVE_SPEED_MS": "DRIVE_SPEED_MS",
}


def cloud_constants() -> dict[str, float]:
    """云侧常量现值（取自 `agg_types`，即运行中的真值）。"""
    return {name: float(getattr(agg_types, name)) for name in CLOUD_TO_END}


def _eval_num(expr: str) -> float | None:
    """解析端侧数值表达式（支持 `6000.0 / 3600.0` 这种写法；不做通用求值，避免 eval）。"""
    parts = [p.strip() for p in expr.split("/")]
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    value = nums[0]
    for n in nums[1:]:
        if n == 0:
            return None
        value /= n
    return value


def read_end_constants(path: Path | None = None) -> dict[str, float] | None:
    """读端侧 `agg_config.uts` 的数值常量；文件不存在 ⇒ `None`（调用方须显式标注"未比对"）。"""
    target = path or (REPO_ROOT / END_CONFIG_REL)
    if not target.exists():
        return None
    text = target.read_text(encoding="utf-8", errors="replace")
    out: dict[str, float] = {}
    for name, expr in _END_CONST_RE.findall(text):
        val = _eval_num(expr.split("//")[0])          # 去掉行尾 `// 注释`
        if val is not None:
            out[name] = val
    return out


def compare_cloud_end(
    cloud: dict[str, float] | None = None,
    end: dict[str, float] | None = None,
) -> list[str]:
    """返回不一致项描述（空列表＝逐项一致）。缺项也算不一致（防止单侧删常量蒙混）。"""
    cloud = cloud if cloud is not None else cloud_constants()
    end = end if end is not None else read_end_constants()
    if end is None:
        return [f"端侧常量源缺失（{END_CONFIG_REL.as_posix()}），无法比对"]
    mismatches: list[str] = []
    for cloud_name, end_name in CLOUD_TO_END.items():
        c_val = cloud.get(cloud_name)
        e_val = end.get(end_name)
        if e_val is None:
            mismatches.append(f"端侧缺 {end_name}")
            continue
        if c_val is None:
            mismatches.append(f"云侧缺 {cloud_name}")
            continue
        if abs(c_val - e_val) > 1e-9:
            mismatches.append(f"{cloud_name}({c_val}) ≠ end.{end_name}({e_val})")
    return mismatches
