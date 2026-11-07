#!/usr/bin/env python3
"""Generate the C-5 story upgrade table and C-6 239-item test ledger."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_LIST = ROOT / "忆述光华_交付文档" / "忆述光华_测试清单.md"
OUT_DIR = ROOT / "docs" / "parallel-dev-收尾"
C5_OUT = OUT_DIR / "25_C-5_21条故事补级表_20261023.md"
C6_OUT = OUT_DIR / "26_C-6_239项测试四态台账_20261023.md"


C5_ROWS = [
    ("US-08", "B", "真机验证同一照片同时属于多个事件及三入口归属",
     "型号/时间：缺；日志/截图：`backend/tests/test_events.py`、"
     "`test_event_items.py`；判定：B 级代码/集成证据，未补 A", "未补级"),
    ("US-09", "B", "真机验证稀疏记录进入日卡片并显示正确条数",
     "型号/时间：缺；日志/截图："
     "`backend/tests/test_event_aggregation_scripts.py`；判定：B 级聚合测试，未补 A", "未补级"),
    ("US-11", "B", "真机验证封面与标题编辑，并确认后台聚合不覆盖用户修改",
     "型号/时间：缺；日志/截图：`client/pages/detail/detail.uvue`；判定：代码路径存在，未补 A", "未补级"),
    ("US-20", "C", "接入 P6 第二设备拨打来电，验证录音暂停、恢复和落盘",
     "型号/时间：待 P6；日志/截图："
     "`scripts/realdevice/checklist_02_录音中断恢复.md`；判定：缺第二设备，待补",
     "待补（缺前置 P6）"),
    ("US-21", "C", "产品部提供正式关怀文案库，真机验证高置信低落情绪触发与节流",
     "型号/时间：可执行；日志/截图：`docs/copy_library/`；判定：默认文案兜底，正式文案未到", "待补（缺产品部文案）"),
    ("US-22", "B", "真机长按/编辑纠错，核对 UI、correction_log 与 content_class 同源",
     "型号/时间：缺；日志/截图：`backend/tests/test_correction.py`；判定：B 级代码/测试证据，未补 A", "未补级"),
    ("US-23", "B", "连续填写同类内容，验证同类纠错应用与 7 天统计口径",
     "型号/时间：缺；日志/截图：`backend/tests/test_correction.py`；判定：B 级测试，测量口径未设备化", "未补级"),
    ("US-24", "B", "构造低置信内容并完成回响联动确认，核对三次一致后生效",
     "型号/时间：缺；日志/截图：`backend/tests/test_echo.py`；判定：B 级回响测试，未补 A", "未补级"),
    ("US-29", "B", "用真机 + 当前后端跑重复搜索，记录 P50/P95/P99",
     "型号/时间：缺；日志/截图："
     "`research/rag_benchmark/evaluation_report.json`；判定：已有后端基准，未补真机 A",
     "未补级"),
    ("US-30", "B", "真机组合验证时间、地点、类型过滤及 NER 结果",
     "型号/时间：缺；日志/截图：`backend/tests/test_search_filters.py`；判定：B 级过滤测试，未补 A", "未补级"),
    ("US-31", "C", "配置企微三件套后验证微信图片/文字转发入库与幂等",
     "型号/时间：待凭证；日志/截图：`backend/tests/test_wechat*.py`；"
     "判定：沙箱代码就绪，生产未验", "待补（缺企微凭证）"),
    ("US-32", "C", "企微凭证到位后验证微信内搜索与 3s/10s 门禁",
     "型号/时间：待凭证；日志/截图：`backend/tests/test_wechat*.py`；判定：实网路径未执行", "待补（缺企微凭证）"),
    ("US-33", "C", "配置 AppID/Secret 后验证 code2session、自动打标和敏感排除",
     "型号/时间：待凭证；日志/截图：`backend/tests/test_wechat*.py`；判定：适配器就绪，实网未验", "待补（缺企微凭证）"),
    ("US-39", "B", "真机触发内容分类后核对画像枚举增量、证据锚点和节流",
     "型号/时间：缺；日志/截图：`backend/tests/test_profile_annotator.py`；判定：B 级画像测试，未补 A", "未补级"),
    ("US-43", "B", "真机验证追问/回响触发敏感护栏并核对违规回流",
     "型号/时间：缺；日志/截图：`backend/tests/test_event_sensitive.py`、"
     "`test_echo.py`；判定：B 级安全测试，未补 A", "未补级"),
    ("US-44", "C", "接入阿里云内容安全并跑合规样本、失败降级和审计",
     "型号/时间：待云服务；日志/截图：外部密钥未配；判定：当前仅有敏感识别替代", "待补（缺阿里云内容安全）"),
    ("US-45", "B", "真机拍照后在另一端查看同一图片镜像与元数据",
     "型号/时间：缺第二端；日志/截图：`backend/tests/test_photo_content.py`；判定：B 级存储测试，未补 A", "未补级"),
    ("US-49", "B", "真机删除内容后验证 30 天软删除、回收站恢复及到期清理",
     "型号/时间：缺；日志/截图：`backend/tests/test_event_ops.py`；判定：B 级操作测试，未补 A", "未补级"),
    ("US-50", "B", "构造双端字段冲突，验证 LWW 收敛和冲突提示",
     "型号/时间：缺第二端；日志/截图：`backend/tests/test_event_sync.py`；判定：B 级同步测试，未补 A", "未补级"),
    ("US-52", "C", "P6 来电中断下验证前台服务、录音恢复与无感续录",
     "型号/时间：待 P6；日志/截图：tracker19 D-06；判定：状态机证据为 C，缺真机 A", "待补（缺前置 P6）"),
    ("US-53", "B", "真机接收消息中心推送并验证 22:00 复盘、回响和深链跳转",
     "型号/时间：缺；日志/截图：`backend/tests/test_echo.py`；判定：应用内消息已有 B 级证据，厂商推送未配", "未补级"),
]


EVIDENCE = {
    "API": "B线待提供；`backend/tests/`（API/RQ/管线）",
    "RET": "B线待提供；`backend/tests/test_search*.py`、`test_image_search.py`、`test_rag.py`",
    "AGG": "B线待提供；`backend/tests/test_events.py`、`test_event_*.py`",
    "CLS": "B线待提供；`backend/tests/test_correction.py`、`test_setfit.py`",
    "PRF": "B线待提供；`backend/tests/test_profile_*.py`",
    "VOI": "C线真机/后端联合；`docs/parallel-dev-收尾/20_R2复验与C线测试执行台账.md`",
    "SYNC": "C线真机 + `backend/tests/test_event_sync.py`",
    "SAF": "B线待提供；`backend/tests/test_echo.py`、`test_event_sensitive.py`",
    "DEV": "C线；`docs/parallel-dev-收尾/20_R2复验与C线测试执行台账.md`",
    "WIN": "不适用；MVP 已移除 Windows 桌面端",
    "WX": "未执行；缺企微三件套/AppID/Secret；候选 `backend/tests/test_wechat*.py`",
    "AUTH": "B线待提供；`backend/tests/test_auth*.py`",
    "DR": "未执行；`deploy/RUNBOOK.md` 灾备演练",
    "PERF": "B/C 线待提供；`research/rag_benchmark/evaluation_report.json`、真机日志",
    "COMP": "C线待提供；真机矩阵与 tracker19/20",
    "REG": "B/C 线回归待提供；`progress.md` 门禁段",
    "PUSH": "未执行；uni-push 厂商通道未配置；候选 `backend/tests/test_echo.py`",
}


OVERRIDES = {
    "API-001": ("已过", "07/08 报告：首批 6.0s ≤30s；`r2_4b_verify.ps1` 历史证据"),
    "AGG-002": ("已过", "`20_R2复验与C线测试执行台账.md` §11.1；103 DB 端侧 L1 事件存在"),
    "SYNC-012": ("已过", "`20_R2复验与C线测试执行台账.md` §12.2：无 WiFi/蜂窝暂缓、WiFi 恢复补传成功"),
    "DEV-001": ("已过", "`20_R2复验与C线测试执行台账.md` §3/§4；103 相册监听与自动导入日志"),
    "DEV-002": ("已过", "`20_R2复验与C线测试执行台账.md` §12.2；103 重启后监听重挂"),
    "COMP-003": ("未过", "仅 PKX110/OPPO 实测；缺小米/华为/vivo 矩阵，`20...` §8"),
}


def parse_tests() -> list[dict[str, str]]:
    pattern = re.compile(
        r"^\| ((?:API|RET|AGG|CLS|PRF|VOI|SYNC|SAF|DEV|WIN|WX|AUTH|DR|PERF|COMP|REG|PUSH)-\d{3}) "
        r"\| ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$"
    )
    rows: list[dict[str, str]] = []
    for line in TEST_LIST.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        test_id, item, validates, priority, kind = (part.strip() for part in match.groups())
        module = test_id.split("-", 1)[0]
        state = "不适用" if module == "WIN" else "未执行"
        evidence = EVIDENCE[module]
        note = ""
        if module == "WX":
            note = "缺企微三件套/AppID/Secret"
        elif module in {"API", "RET", "AGG", "CLS", "PRF", "SAF", "AUTH"}:
            note = "纯后端项由 B 线提供状态"
        elif module == "WIN":
            note = "桌面端已移出 MVP"
        elif module in {"DR", "PUSH"}:
            note = "缺部署/推送前置"
        if test_id in OVERRIDES:
            state, evidence = OVERRIDES[test_id]
            note = "已有证据"
        rows.append({
            "id": test_id,
            "item": item,
            "validates": validates,
            "priority": priority,
            "type": kind,
            "state": state,
            "evidence": evidence,
            "note": note,
        })
    return rows


def md_escape(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ")


def generate_c5() -> None:
    lines = [
        "# C-5 · 21 条 B/C 级用户故事补级表",
        "",
        "> 日期：2026-10-23",
        "> 口径：53 条用户故事减去 32 条 A 级，剩余 14 条 B 级 + 7 条 C/🟡，共 21 条。",
        "> 计划源 `技术文档/02_计划与分工/忆述光华_后续推进计划_20260923.md` 在本机未找到；"
        "本表用 `07_晚间例会汇报_20260827.md` §6.3 和 "
        "`08_收尾波次完成汇报_20260828.md` §1.2/§1.5 做可核对推导。",
        "> A 级准入沿用三要素：型号+时间 / 截图或日志 / 判定；缺一不写 A。",
        "",
        "| US 编号 | 当前级别 | 补级动作 | 证据三要素 | 结论 |",
        "|---|---|---|---|---|",
    ]
    for us, level, action, evidence, conclusion in C5_ROWS:
        lines.append(
            f"| {us} | {level} | {md_escape(action)} | {md_escape(evidence)} | {md_escape(conclusion)} |"
        )
    lines += [
        "",
        "## 汇总",
        "",
        "- 共 21 条：B 级 14 条，C/🟡 7 条。",
        "- 允许挂起：US-20、US-52（P6 第二设备），US-21（产品部文案），"
        "US-31/32/33（企微凭证），US-44（阿里云内容安全）。",
        "- 其余 14 条仍需补 R2 真机三要素后才能升级为 A。",
    ]
    C5_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_c6(rows: list[dict[str, str]]) -> None:
    counts = Counter(row["state"] for row in rows)
    module_counts = Counter(row["id"].split("-", 1)[0] for row in rows)
    lines = [
        "# C-6 · 239 项测试四态归集台账",
        "",
        "> 日期：2026-10-23",
        "> 起账依据：`docs/parallel-dev-收尾/20_R2复验与C线测试执行台账.md`",
        "> 源清单：`忆述光华_交付文档/忆述光华_测试清单.md`",
        "> 四态：已过 / 未过 / 未执行 / 不适用。纯后端 pytest"
        "（API/RET/AGG/CLS/PRF/SAF/AUTH 等）由 B 线提供结果，本表只归集与标缺口。",
        "",
        "## 汇总",
        "",
        f"- 总项数：{len(rows)}",
        f"- 已过：{counts['已过']}",
        f"- 未过：{counts['未过']}",
        f"- 未执行：{counts['未执行']}",
        f"- 不适用：{counts['不适用']}",
        "",
        "| 模块 | 项数 |",
        "|---|---:|",
    ]
    for module in sorted(module_counts):
        lines.append(f"| {module} | {module_counts[module]} |")
    lines += [
        "",
        "## 明细",
        "",
        "| 编号 | 测试项 | 验证内容 | 优先级 | 类型 | 四态 | 证据/归属 | 备注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {id} | {item} | {validates} | {priority} | {type} | {state} | {evidence} | {note} |".format(
                **{key: md_escape(value) for key, value in row.items()}
            )
        )
    C6_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"C6 rows={len(rows)} counts={dict(counts)}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    generate_c5()
    rows = parse_tests()
    if len(rows) != 239:
        raise SystemExit(f"expected 239 test rows, got {len(rows)}")
    generate_c6(rows)
    print(C5_OUT)
    print(C6_OUT)


if __name__ == "__main__":
    main()
