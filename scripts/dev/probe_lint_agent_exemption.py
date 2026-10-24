"""负向探针：确认撤销 agent/ 豁免后，lint 门禁**真的能命中** agent/ 下的违规。

铁律见 skills/harness-gate-maintenance/SKILL.md 第 1 条：
「新增门禁必须先做负向探针自校验」——撤销豁免等价于把一段"以前从不过门禁的代码"
纳入判据，同样必须证明它**不是假绿**。
"""

import os
import sys

sys.path.insert(0, "scripts")

import review_agent as ra

PROBE = "agent/_probe_lint_negative.py"

# F821 undefined name + I001 unsorted imports：确定性命中，不依赖版本差异
BODY = "import sys\nimport os\n\nx = undefined_name\n"

with open(PROBE, "w", encoding="utf-8") as f:
    f.write(BODY)
try:
    ok, out = ra.check_lint([PROBE])
    print("[probe] gate verdict:", "PASSED (⇒ 门禁失明，撤销豁免是假绿!)" if ok else "BLOCKED (⇒ 门禁真的在看 agent/)")
    print("[probe] output head:")
    for line in out.splitlines()[:8]:
        print("   ", line)
finally:
    os.remove(PROBE)

# 阳性对照：同一个文件放在非豁免路径下，行为应一致
print("[probe] _lint_skip(agent/x.py) =", ra._lint_skip("agent/x.py"))
