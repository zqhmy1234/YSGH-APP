#!/bin/bash
set -e

echo "=== 忆述光华 Harness 初始化 ==="
cd "$(dirname "$0")"

# 1. 交付文档完整性检查（18 份必须存在）
DOCS=(
  "忆述光华_交付文档/忆述光华_开工总结.md"
  "忆述光华_交付文档/忆述光华_MVP方案_v3.md"
  "忆述光华_交付文档/忆述光华_开发决策清单.md"
  "忆述光华_交付文档/忆述光华_开发规划+分工.md"
  "忆述光华_交付文档/忆述光华_测试清单.md"
  "忆述光华_交付文档/忆述光华_数据库Schema设计.md"
  "忆述光华_交付文档/忆述光华_外部API清单与成本.md"
  "忆述光华_交付文档/忆述光华_产品部验收标准更新转达稿.md"
  "忆述光华_交付文档/忆述光华_深度开发设计/01_用户画像系统_B1.md"
  "忆述光华_交付文档/忆述光华_深度开发设计/02_RAG多路检索_B2.md"
  "忆述光华_交付文档/忆述光华_深度开发设计/02b_RAG范式前沿补充.md"
  "忆述光华_交付文档/忆述光华_深度开发设计/03_照片事件聚合_B3.md"
  "忆述光华_交付文档/忆述光华_深度开发设计/04_数据同步与离线优先_B4.md"
  "忆述光华_交付文档/忆述光华_深度开发设计/05a_语音双通道_B5a.md"
  "忆述光华_交付文档/忆述光华_深度开发设计/05b_安全护栏_B5b.md"
  "忆述光华_交付文档/忆述光华_深度开发设计/05c_分类纠错_B5c.md"
  "忆述光华_交付文档/忆述光华_深度开发设计/05d_后台任务与录音_B5d.md"
  "忆述光华_交付文档/忆述光华_深度开发设计/05e_Windows桌面端_B5e.md"
)

echo "--- 检查交付文档（${#DOCS[@]} 份）---"
MISSING=0
for f in "${DOCS[@]}"; do
  if [ -f "$f" ]; then
    echo "  OK  $f"
  else
    echo "  MISSING  $f"
    MISSING=1
  fi
done

# 2. Harness 文件检查（口径权威链：AGENTS.md「当前状态」+ docs/决策台账.md 为数字/决策/待拍板唯一来源）
echo "--- 检查 harness 文件 ---"
for f in AGENTS.md STATE.md feature_list.json progress.md session-handoff.md \
         init.sh init.ps1 docs/决策台账.md docs/lessons.md docs/lessons-主题索引.md; do
  if [ -f "$f" ]; then
    echo "  OK  $f"
  else
    echo "  MISSING  $f"
    MISSING=1
  fi
done

# 2.5 STATE.md 新鲜度（软告警，不阻断）—— 与 init.ps1 对等
echo "--- STATE.md 新鲜度 ---"
if [ -f STATE.md ]; then
  STATE_DATE=$(grep -oE '最后更新[^0-9]*[0-9]{4}-[0-9]{2}-[0-9]{2}' STATE.md | head -1 | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}')
  HEAD_DATE=$(git log -1 --format=%cs 2>/dev/null || echo "")
  if [ -z "$STATE_DATE" ]; then
    echo "  WARN   STATE.md 缺『最后更新: YYYY-MM-DD』行"
  elif [ -n "$HEAD_DATE" ] && [ "$STATE_DATE" \< "$HEAD_DATE" ]; then
    echo "  WARN   STATE.md 最后更新 $STATE_DATE 早于最新提交 $HEAD_DATE —— 状态可能已漂移"
  else
    echo "  OK  STATE.md 最后更新 $STATE_DATE"
  fi
else
  echo "  WARN   STATE.md 不存在（当前状态无唯一载体）"
fi

# 3. Git 状态
echo "--- Git 状态 ---"
if [ -d .git ]; then
  git status --short
  echo "  Git 仓库存在（$(git branch --show-current 2>/dev/null || echo 'no branch')）"
else
  echo "  WARN: 非 Git 仓库"
fi

if [ "$MISSING" = "1" ]; then
  echo "!!! 初始化失败：存在缺失文件"
  exit 1
fi

echo "=== 初始化通过 ==="
echo ""
echo "下一步："
echo "1. 读 STATE.md —— 唯一「当前状态」（正在做什么 / 下一步 / 阻塞 / 关键数字）"
echo "2. 读 docs/决策台账.md §5 待拍板与 §7 未关闭缺陷"
echo "3. 读 feature_list.json 看特性状态（约定只允许 1 条 in-progress）"
echo "4. 完成前重跑本脚本 + python scripts/review_agent.py --full 验证"
