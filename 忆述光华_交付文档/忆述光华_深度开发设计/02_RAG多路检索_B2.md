# B2 · RAG 多路检索 + 范式调研（深度开发设计定稿）

> 所属：忆述光华 MVP 深度开发功能（⭐⭐）
> 版本：v1｜日期：2026-08-14｜grill-me + deep research：峰宝 × 北嘎霖
> 状态：✅ B2 选型收敛 + RAG 范式调研完成（4 子 agent 并行，腾讯技术工程等大厂公众号已覆盖）
> 配套：外部 API 清单（Qdrant/API 成本见《忆述光华_外部API清单与成本.md》）

---

## 1. 为什么这个功能需要深度设计

描述性搜索是 **Q0 验证核心**（"比手机整理更好用"）。且 RAG 范式直接决定：
- MVP：搜索的 Top3 命中率（≥70% 验收）
- 后期：与用户的**深度交流/深度理解**质量（复盘、追问、回响、回忆录都要靠它）

产品输入输出：**多模态**（照片/文字/语音/文章）+ 需要**多跳推理**（"我和小张去年吃的饭里哪家我最喜欢"需跨照片+语音+文字关联）——Advanced RAG（简单检索+重排）不够。

## 2. 已拍板选型（grill-me 收敛）

| # | 决策 | 结论 |
|---|---|---|
| B2-1 | 向量库 | **Qdrant**（payload 过滤一等公民 + 原生 dense+sparse 混合检索 + Rust 单机轻量；2026 scored.tools 开源生产 RAG 9.4/10 第一） |
| B2-1 | 文本 Embedding | **BGE-M3**（dense+sparse 三合一，"上海/苏州"关键词精确匹配是描述性搜索刚需；中文 RAG 国民款） |
| B2-2 | 召回结构 | 语义路(dense)+关键词路(sparse)走 Qdrant RRF 融合；**时间/类型/地点走 payload filter（不是召回路）**；NER 实体存 payload tag；跨栏目**单 collection + content_type 字段** |
| B2-3 | Reranker | **bge-reranker-v2-m3**（开源、中文强、和 BGE-M3 同族；不用 LLM 重排——贵且慢） |
| B2-4 | 文字搜图 | **MVP 必须做**（差异化卖点）→ Qwen3-VL-Embedding **图片塔**（API：tongyi-embedding-vision-plus $0.09/百万，首月含免费额度；不自部署——要 GPU 不划算）；Qdrant **named vectors**（text_vec + image_vec）；查询路由判断文本/图片意图；**以图搜图顺带支持**（同空间）；成本 +16 元/月 |
| — | 画像 | 直接注入不压缩不走 RAG（Q37 已定，B1 细化三层披露）——画像管"状态"，RAG 管"事实" |

## 3. RAG 范式深度调研结论（4 子 agent，2026-08）

### 3.1 范式全景（生产可用性视角）

| 范式 | 代表 | 生产可用 | 多跳 | 关键结论 |
|---|---|---|---|---|
| 混合检索+Rerank | 阿里百炼/美团标配 | ✅✅ 大厂底线 | 单跳 | 向量+BM25+RRF+Rerank+Query 改写+溯源 = **2025-26 绝对标配** |
| LightRAG | 轻量图 RAG（MIT,~38k star） | ✅ CPU 可跑、增量、维护最活跃 | 强 | MVP 基底候选；GraphRAG 成本降 99% |
| Graphiti/Zep | 时序知识图谱（~27k） | ✅ 240+ 付费客户、<200ms | 强 | **专为 Agent 记忆设计**，双时间模型（事实何时失效） |
| GraphRAG | 微软（~35k） | ⚠️ 索引成本 350-1000×，官方称"研究代码" | 原生强 | 腾讯优图自研 Youtu-GraphRAG 才生产可用（成本省 30%） |
| HippoRAG2 | 神经符号（PPR 游走） | ❌ 需 GPU、偏学术 | 极强 | 任务匹配但生产性差 |
| Agentic RAG | LangGraph/LlamaIndex | ⚠️ 腾讯 ADP3.0 已落地 | 强 | **Token 3-10×，须路由分层+硬性迭代上限**（Gartner：40% agentic 项目将被取消） |
| Mem0 | 记忆层（~58k） | ✅ | 弱 | 管"状态记忆"，与 RAG 分治（大厂共识） |

### 3.2 大厂共识（腾讯技术工程/阿里百炼/字节/美团/华为云）

1. **混合检索+Query 改写+Rerank+原文溯源 = 生产底线**，无一例外（阿里 92% 精度、美团 NDCG 0.72→0.81）
2. **"长上下文取代 RAG"已被证伪** → 共识是 Context Engineering（检索优先 + 长上下文承载）
3. **Agentic RAG 仅限多跳/跨文档复杂问题**，必须路由分层（简单→naive RAG，复杂→Agentic/图增强）
4. **GraphRAG 有条件真用**：多跳问题占比 >25% 才值得；社区版"效率低、难生产"（腾讯优图原话）
5. **记忆管状态、RAG 管事实**——Mem0 类状态记忆（ADD/UPDATE/DELETE 冲突消解）+ RAG 管检索事实
6. **多模态走"多路分治+融合"**（UniDoc-Bench：融合 0.684 > 纯文本 0.653 > 联合嵌入 0.641）；图片"caption 双写"（视觉→文本进文本路 + 图片向量进图像路）
7. **上线即建评测**：faithfulness≥0.9、relevancy≥0.85、context precision≥0.8

### 3.3 腾讯 ima 参照（2026.7 拆解）——和忆述光华最像的产品

微信生态企业级 RAG 工作台：**多模态入库（19 格式/OCR）+ 混合检索 + Query 改写 + Rerank + 溯源 + 动态知识图谱（300+ 关系）+ 双模型调度 + Agent 任务模式**——验证了"先转结构化文本再入链最稳"的工程路径。

## 4. 忆述光华 RAG 范式推荐（MVP + 演进）

### 4.1 MVP 架构（范式 = 混合检索 + 路由分层，不上图谱）

```
查询
 ├─ Query 改写（LLM：时间表达解析"去年夏天"→过滤条件；实体抽取→tag）
 ├─ 路由（轻量分类）
 │   ├─ 文本意图 → BGE-M3(dense+sparse) → Qdrant text_vec（RRF 融合）
 │   ├─ 图片意图 → Qwen3-VL 文本塔 → Qdrant image_vec
 │   └─ 混合 → 两路都跑 → RRF 融合
 ├─ payload filter（时间/类型/地点/实体 tag）——过滤层
 ├─ bge-reranker-v2-m3 精排 top-50 → top-10
 └─ 溯源（返回带原文引用——大厂标配）
```

**MVP 明确不上**：
- ❌ GraphRAG/知识图谱（多跳占比未验证，建图成本 350-1000×，腾讯都承认社区版难生产）
- ❌ Agentic RAG 默认开启（Token 3-10×，仅复杂问题按路由启用）
- ❌ 长上下文直吞（Q22 讨论过作为备选，但大厂共识检索优先）

### 4.2 演进路径（MVP → 深度理解）

| 阶段 | 触发条件 | 升级 |
|---|---|---|
| MVP 后 | 多跳查询占比 >25%（上线即埋点统计） | 引入 **LightRAG**（轻量图谱，增量更新，替代自研图谱成本） |
| 深度理解期 | 需要"事实何时失效"（如"她现在还健身吗"） | **Graphiti/Zep**（时序图谱，Agent 记忆专用） |
| 视觉增强 | 复杂文档/截图检索精度不足 | ColQwen2（视觉检索，ViDoRe 89.3%，免 OCR） |

### 4.3 关键设计约束（大厂共识 → 忆述光华）

1. **画像=状态，RAG=事实，两者分治**（Q37 画像直接注入已定；RAG 管事件流检索）——大厂共识验证了这个拍板
2. **Query 改写是 MVP 必做**（"去年夏天"时间解析、"苏州"实体抽取 → 转过滤条件）——B2-2 的 NER + 时间解析落在这里
3. **溯源是信任底线**（记忆类产品，用户要能核对"AI 说的依据"）——呼应"不出现幻觉"验收
4. **上线即建评测集**（50 条真实查询：faithfulness/relevancy/context precision）——M2 验收前就要有

## 5. 成本影响（更新）

| 项 | 变化 |
|---|---|
| 文字搜图（图片塔 API） | +16 元/月（100 用户） |
| 查询路由 + LLM 精排 | +5 元/月（qwen-flash，路由 ~200 token + 精排 ~500 token/搜索） |
| **MVP 合计** | **≈ 317-352 元/月**（对齐《外部API清单与成本》最终口径，服务器按真实套餐） |

> 注：此前 438 元按服务器 150 元/月估算；服务器改真实套餐价（3-38 元/月）后，总成本为 317-352 元/月。

## 6. 参考来源

- Qdrant 生产 RAG 2026 评测（9.4/10 第一）：https://scored.tools/blog/best-open-source-vector-databases-production-rag-2026
- BGE-M3 三合一（dense+sparse+multi-vector）：https://devpress.csdn.net/v1/article/detail/158703377
- Qwen3-VL-Embedding（阿里 2026-01 开源，双塔统一空间）：https://github.com/QwenLM/Qwen3-VL-Embedding ；API 计费：https://www.alibabacloud.com/help/tc/model-studio/model-introduction-6
- 腾讯优图 RAG 万字长文（Youtu-GraphRAG）：https://news.qq.com/rain/a/20250908A06WBU00
- 腾讯 ADP3.0 Agentic RAG：https://csdn.net/article/2025-09-17/151799244
- 腾讯 ima 拆解（多模态 RAG 工作台）：https://developer.cloud.tencent.com/article/2703790
- 阿里百炼 RAG 实战：https://developer.aliyun.com/article/1743969
- 美团混合检索：https://paicoding.com/column/10/29
- 字节 DataMind GraphRAG（VLDB 2025，50 场景）：https://arxiv.org/pdf/2604.02861v1
- GraphRAG 成本量化：https://juejin.cn/post/7670764284415393828
- Agentic RAG 生产指南（成本/失败模式）：https://tianpan.co/zh/blog/2026-02-11-agentic-rag-architecture-production-guide
- 长上下文 vs RAG 共识：https://m.aitntnews.com/newDetail.html?newId=20867
- Mem0 记忆架构（状态记忆分治）：https://devpress.csdn.net/v1/article/detail/156336689

## 7. 待续

- B3 照片事件聚合算法（下一轮 grill）
- 查询路由的轻量实现细节（规则 vs 小模型）——可并入 B3 后或独立
- 上线评测集设计（50 条真实查询 + 3 指标基线）
