#!/bin/bash
set -e

echo "=== 忆述光华 Harness 初始化 ==="
cd "$(dirname "$0")"

# 1. 交付文档完整性检查（18 份必须存在）
DOCS=(
  "忆述光华_交付文档/忆述光华_开工总结README.md"
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

# 2. Harness 文件检查
echo "--- 检查 harness 文件 ---"
for f in AGENTS.md feature_list.json progress.md init.sh session-handoff.md; do
  if [ -f "$f" ]; then
    echo "  OK  $f"
  else
    echo "  MISSING  $f"
    MISSING=1
  fi
done

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
echo "1. 读 feature_list.json 看特性状态"
echo "2. 从 feature_list.json 选一个未完成特性"
echo "3. 只实现该特性"
echo "4. 完成前重跑 ./init.sh 验证"
