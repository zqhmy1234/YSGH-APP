"""Render the judge JSON into the human-readable module evidence sheet."""

from __future__ import annotations

import json
from pathlib import Path


def render(report_path: Path, out_path: Path) -> Path:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    lines: list[str] = [
        "# agentv3 模块测试说明（机器生成，勿手改）",
        "",
        f"- 生成时间（UTC）：`{data['generated_at']}`",
        f"- 审判结论：**{data['verdict']}**（静态 P0 = {sum(1 for f in data['static'] if f['severity'] == 'P0')}，"
        f"套件 exit = {data['suite']['exit']}，变异被杀 {data['mutation']['killed']}/{data['mutation']['total']}）",
        "- 复跑：`cd agent && PYTHONPATH=src /tmp/ghvenv/bin/python -m agentv3.judge.run`",
        "",
        "## 逐模块：测试 → 输入 → 输入是否有效 → 输出 → 输出是否有效",
        "",
        "| 模块 | 测试文件 | 通过/失败 | 输入 | 输入是否有效（判据） | 输出 | 输出是否有效（判据） | 结论 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in data["evidence"]:
        tests = f"`{row['tests']}`"
        counts = f"{row['passed']}/{row['failed']}"
        lines.append(
            f"| **{row['module']}** | {tests} | {counts} | {row['input']} | {row['input_validity']} | {row['output']} | {row['output_validity']} | {_badge(row['verdict'])} |"
        )

    lines += [
        "",
        "## 变异测试（10 个手写变异体，全部必须被测试杀死）",
        "",
        "| 变异体 | 文件 | 负责杀它的测试 | 结果 |",
        "|---|---|---|---|",
    ]
    survivors = {s["label"] for s in data["mutation"]["survivors"]}
    for item in data["mutation"].get("items", ()):
        label = item["label"]
        mark = "杀死 ✅" if item["status"] == "killed" else f"存活 ❌（{item['status']}）"
        lines.append(f"| `{label}` | `{item.get('file', '—')}` | `{item.get('test', '—')}` | {mark} |")

    probe = data.get("probes", {"total": 0, "ok": 0, "items": []})
    lines += [
        "",
        f"## 八条负向探针（先红后绿，{probe['ok']}/{probe['total']}）",
        "",
        "| 探针 | 削弱后必须变红 | 恢复后必须变绿 | 结论 |",
        "|---|---|---|---|",
    ]
    for item in probe["items"]:
        lines.append(f"| `{item['label']}` | 是 | {'是' if item['status'] == 'ok' else '否'} | {'✅ 有效' if item['status'] == 'ok' else '❌ ' + item['status']} |")

    static = data["static"]
    lines += ["", "## 静态检查", ""]
    if static:
        lines += ["| 检查 | 级别 | 详情 | 复现 |", "|---|---|---|---|"]
        lines += [f"| {f['check']} | {f['severity']} | {f['detail']} | `{f.get('repro', '')}` |" for f in static]
    else:
        lines.append("无发现（导入图、契约常量、禁语、占位符、Agent 测试覆盖五项均为空）。")

    lines += ["", "## 如何独立复核", "", "```bash", "cd agent", "PYTHONPATH=src /tmp/ghvenv/bin/pytest tests/agentv3 -q", "PYTHONPATH=src /tmp/ghvenv/bin/python -m agentv3.judge.run", "```", ""]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def _badge(verdict: str) -> str:
    return {"ok": "✅ 已验", "failed": "❌ 失败", "unverified": "⚠️ 未验"}.get(verdict, verdict)


_MUTATION_LABELS = (
    "correction-precedence",
    "batch-idempotency",
    "evidence-pruned-first",
    "degrade-not-empty",
    "llm-down-zero-calls",
    "private-claim-needs-citation",
    "digest-unavailable",
    "crisis-short-circuit",
    "sculptor-refuses-bad-batch",
    "sqlite-write-through",
)


if __name__ == "__main__":  # pragma: no cover
    root = Path(__file__).resolve().parents[3].parent
    render(root / "docs" / "agent-v3" / "judge-report.json", root / "docs" / "agent-v3" / "模块测试说明.md")
