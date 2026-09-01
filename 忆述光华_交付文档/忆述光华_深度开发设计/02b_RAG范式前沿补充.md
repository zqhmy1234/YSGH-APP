# B2b · RAG 范式前沿补充调研（10 篇深度文章拆解）

> 所属：忆述光华 MVP 深度开发功能（B2 RAG 多路检索）
> 版本：v1｜日期：2026-08-14｜峰宝提供 10 篇前沿公众号文章，逐一阅读分析
> 结论：**3 条验证既有决策 + 3 条修正决策 + 2 条终局方向**
> 配套：《02_RAG多路检索_B2.md》（基础选型）

---

## 一、十篇文章速览

| # | 文章 | 来源/日期 | 核心内容 |
|---|---|---|---|
| 1 | ACL 2026 RAG 论文速览③（可信时代） | 知识工场 08-07 | UniversalRAG（模态分治）/ ProbeRAG（隐空间冲突探测）/ GRIP（检索控制内化）/ RouteRAG（RL 路由）/ RLSeek（证据落地幻觉检测） |
| 2 | 腾讯 WeKnora 拆解（多路 RRF 召回） | 小码AI笔记 07-12 | 加权 RRF（vector 0.7/keyword 0.3, k=60）、FAQ/文档分叉、8 步合并流水线、引用回查；1.8 万 star |
| 3 | HG-RAG：层级引导图检索 | PaperRAG 07-20 | 结构边/关系边区分、锚点爬图（k_up=2）、15 节点 cap；图越大优势越明显（大图 1.86 vs baseline 0.02） |
| 4 | 清华 TAP-RAG：任务感知策略控制 | PaperRAG 07-23 | 10 字段 policy、TAPC 任务路由 + 视觉门控（视觉不能自由覆盖文本）；TAPC alone +8.5 |
| 5 | RAGFlow：8.6 万 star 企业知识库 | 极客之家 07-24 | 版面理解、十几种切分、溯源、挂钉钉企微飞书；硬件 16G 起（对我们是硬伤） |
| 6 | Blockify：数据层 RAG（别再分块） | 码间AI楠哥 05-03 | IdeaBlock（问答对+元数据+实体）、Ingest+Distill 两阶段、LSH 聚类去重；40x 压缩 99%+ 保真、3B 小模型 260% 提升 |
| 7 | Enterprise RAG Challenge 夺冠方案 | 数智知客 06-27 | Ilya Rice 五阶段：Docling 深改、父页找回、**LLM 精排（0.7 LLM+0.3 向量）**、模块化 Prompt、双重路由、SO Reparser；实测混合搜索反而降质 |
| 8 | Zvec：阿里"向量版 SQLite" | 机器回廊 06-17 | 嵌入式向量库（pip install）、Proxima 引擎、十亿级毫秒检索、进程内零网络、FTS+DiskANN、v0.5 支持 Go/Rust |
| 9 | PageIndex：扔掉向量数据库 | AI撬动地球 06-10 | LLM 树搜索（in-context 层次索引）、FinanceBench 98.7%（向量 RAG 30-50%）、交叉引用跟随、多步推理；慢（秒级）贵（多轮 LLM） |
| 10 | Palantir OAG：本体论增强生成 | 乱纪元笔记 07-12 | RAG 检索文本 vs OAG 操作语义（结构化对象）；五层 Agent 架构（Context/Query/Logic/Action/Governance）；确定性与概率性分离 |

---

## 二、验证既有决策（3 条，架构方向对了）

### ① 多模态"分治"而非"统一空间"——B2 架构被 ACL 2026 验证
UniversalRAG 证明**模态鸿沟**：统一表示空间检索会系统性偏向同模态内容（文本查询几乎搜不到相关图像）。我们 B2 定的"BGE-M3 文本塔 + Qwen3-VL 图片塔 + 查询路由"正是分治路线——与 2026 最前沿共识一致。

### ② 结构化数据 > 文本块——"事件原子化"被验证
Blockify 的 IdeaBlock（每个知识单元=问答对+元数据+实体）在医疗 RAG 用 3B 小模型跑出 **260% 提升、40x 压缩、99%+ 保真**——"更好的数据 > 更大的模型"。忆述光华的事件本来就是结构化单元（有类型/元数据/时间），方向被验证。

### ③ 大厂生产参数可直接抄
腾讯 WeKnora 加权 RRF：**vector 0.7 / keyword 0.3，rrf_k=60**——B2-2 的 RRF 融合直接用这套生产参数。

---

## 三、修正决策（3 条，需要更新 B2）

### ① 向量库：云侧 Qdrant 保留 + **Windows 端离线检索用 Zvec（阿里嵌入式）**
- Zvec = "向量版 SQLite"：`pip install zvec` 进程内检索，十亿级毫秒，零网络/序列化开销，WAL 持久化，v0.5 有 FTS+DiskANN+Go/Rust SDK
- 忆述光华是**本地优先**产品 → Windows 桌面端离线检索用 Zvec 天然契合（数据不出设备、零运维）
- 云侧单机仍 Qdrant（生产验证 9.4/10）；**双轨：云 Qdrant + 桌面 Zvec**

### ② 查询路由：从"规则"升级为"LLM 任务感知"（TAP-RAG 思想）
- TAP-RAG 的 10 字段 policy 证明"看人下菜"的策略控制拿走全部增益（TAPC alone +8.5，执行器单独加反而掉点）
- 我们的查询路由（文本/图片意图）升级为 **LLM 轻量任务分类**（qwen-flash 一次调用）
- 同时把"搜索/复盘/追问/回响"四类任务的披露策略差异化（接 B1 的 L2 场景扩展）——这就是 TAP-RAG 落地

### ③ Reranker：bge-reranker 保留 + **叠加 LLM 精排兜底**（Ilya 夺冠方案）
- Ilya：向量粗筛 30 → 父页找回 → **LLM 精排（0.7 LLM + 0.3 向量）**——LLM 能判断"这段能不能回答这个问题"
- 且他**实测混合搜索反而降质**（提醒别迷信主流）
- 改法：bge-reranker 粗排 top-50 → top-10 → qwen-flash LLM 精排 → top-5（每查询 +1 次 LLM 调用，成本可控）

---

## 四、终局方向（MVP 不做，架构预留）

### ① Palantir OAG（本体论增强生成）——忆述光华的终局
- OAG：**RAG 检索文本，OAG 操作语义**——LLM 获得"有类型、有关系、有身份"的结构化对象而非文本片段
- **用户的记忆库就是他的 Ontology**：事件=Object、人物/地点=Link Type、人生大事时间线=层级关系
- 五层架构：Context（确定性注入）/ Query / Logic / Action / Governance——"深度理解用户"该有的样子
- B1 的画像+事件结构化方向 = OAG 雏形，**架构预留对象化演进**

### ② PageIndex（扔掉向量库，98.7%）——深度复盘/回忆录场景
- LLM 树搜索（in-context 层次索引），能跟随交叉引用、多步推理，每步留推理痕迹
- 慢（秒级）贵（多轮 LLM），只适合**单份长文档深度提取**——对应"回忆录生成/年度深度复盘"（单用户全量记忆深挖）
- 混合路线：向量搜文档 + PageIndex 深挖

### ③ HG-RAG 层级爬图——"跨事件多跳推理"
- "人生大事时间线"天然有层级（人生阶段→事件→细节）
- 结构边/关系边 + 锚点爬图 = "我和小张的关系怎么变的"的解法
- 支撑 Graphiti/LightRAG 演进路径

---

## 五、更新后的 B2 结论（一句话）

> 分治架构（BGE-M3 + Qwen3-VL + 路由）被 ACL 2026 验证；抄 WeKnora 加权 RRF 参数；查询路由升级 LLM 任务感知（TAP-RAG）；bge-reranker + LLM 精排兜底（Ilya 方案）；Windows 端离线检索加 Zvec 嵌入式；终局方向 Palantir OAG（记忆=个人本体论）。

## 六、文章来源链接

- ACL 2026 RAG 论文速览：https://mp.weixin.qq.com/s/BpuBdJ-3z54hz5fSCKmSjQ
- 腾讯 WeKnora 拆解（RRF 多路召回）：https://mp.weixin.qq.com/s/x3EvQgrDKSDkW3expZWclg
- HG-RAG（层级引导图检索）：https://mp.weixin.qq.com/s/WLrZYlZTs595CvXE7ZFOVg
- 清华 TAP-RAG：https://mp.weixin.qq.com/s/OpaDUyU7uH6JzlC3u2jgjA
- RAGFlow 企业知识库：https://mp.weixin.qq.com/s/PkH_OLiLvtmo1BB0EGUK9A
- Blockify（数据层 RAG）：https://mp.weixin.qq.com/s/awlvqSGsiyNTlF_y6QE5OQ
- Enterprise RAG Challenge 夺冠方案：https://mp.weixin.qq.com/s/o6_-w0omNr9XUl3gTey4Dg
- Zvec（阿里向量版 SQLite）：https://mp.weixin.qq.com/s/8efDcyyr2HWM8Qt07744Wg
- PageIndex（扔掉向量库）：https://mp.weixin.qq.com/s/OkNToMF9X836hAIdWhknoA
- Palantir OAG（本体论增强生成）：https://mp.weixin.qq.com/s/_AQRvXlIZ5hmiBUt2P-L9A

---

## 七、B2 最终决策汇总（合并 02 + 02b）

| 决策 | 结论 |
|---|---|
| 向量库（云侧） | Qdrant（生产验证）+ WeKnora 加权 RRF 参数（0.7/0.3, k=60） |
| 向量库（桌面端） | **Zvec 嵌入式**（本地优先、零运维、数据不出设备） |
| 文本塔 | BGE-M3（dense+sparse，保留） |
| 图片塔 | Qwen3-VL-Embedding（API，文字搜图/以图搜图） |
| 查询路由 | **LLM 任务感知**（TAP-RAG 思想：文本/图片/四类任务路由 + 披露策略差异化） |
| Reranker | bge-reranker-v2-m3 粗排 + **qwen-flash LLM 精排兜底**（Ilya 方案） |
| 过滤层 | payload filter（时间/类型/地点/实体 tag，不是召回路） |
| 跨栏目 | 单 collection + content_type 字段 |
| 溯源 | MVP 必做（大厂标配 + 记忆类产品信任底线） |
| 终局演进 | LightRAG → Graphiti/Zep → **OAG（个人本体论）/ PageIndex（回忆录深挖）** |
