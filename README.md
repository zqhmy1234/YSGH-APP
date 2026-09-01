# YSGH-APP · 忆述光华

个人记忆整理与回顾 APP：自动整理照片/文字/语音碎片记忆，提供 AI 分类、四层事件聚合、描述性搜索（RAG）、时间轴与"去年今日"回响。MVP 目标 100 用户内测。

## 快速入口

- **文档索引**：[docs/README.md](docs/README.md)（规划/设计/契约/RAG/数据质量/审计 全索引）
- **工作规则**：[AGENTS.md](AGENTS.md)（仓库结构 / 技术栈 / 决策）
- **功能清单**：[feature_list.json](feature_list.json) ｜ **进度日志**：[progress.md](progress.md)
- **API 契约**：[docs/OpenAPI契约.md](docs/OpenAPI契约.md)
- **RAG 测评**：[docs/RAG测评报告_20260825.md](docs/RAG测评报告_20260825.md)

## 技术栈

- 后端：Python FastAPI + SQLAlchemy + PostgreSQL + Qdrant（BGE-M3 混合检索）+ RQ worker
- 客户端：uni-app x（Android）+ UTS 插件（相册监听/端侧聚合）
- 外部依赖：阿里云百炼（qwen-flash 改写/路由、Qwen3-VL 图片塔）、腾讯云 COS、高德逆地理、Sentry
- 密钥：Infisical（见 `skills/infisical-secrets`）

## 测试与门禁

- `pytest -m rag`：RAG 检索回归（指标口径见 [RAG测评报告](docs/RAG测评报告_20260825.md)）
- `python -m research.rag_benchmark.run_eval --external`：RAG 全分布测评 + 外部测试集
- 提交前：相关测试 + ruff（full review_agent 可按需跳过，见 AGENTS.md）
