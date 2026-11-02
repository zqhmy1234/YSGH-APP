# 域⑤ · 检索与 RAG 深度审计

审计范围：`backend/app/`（RAG/检索域）+ `client/`（搜索页与搜索 API）+ `scripts/`（RAG 基准/评测）+ `deploy/`（Qdrant 耦合面）。`agent/` 已排除。
本波性质：**严格行为等价**，缺陷**只登记不修**。

---

## 覆盖范围与实读清单

### 后端（实读全文）
| 文件 | 行数 |
|---|---|
| `backend/app/api/search.py` | 81 |
| `backend/app/schemas/search.py` | 41 |
| `backend/app/services/rag/__init__.py` | 252 |
| `backend/app/services/rag/recall.py` | 170 |
| `backend/app/services/rag/pg_fallback.py` | 89 |
| `backend/app/services/rag/image.py` | 148 |
| `backend/app/services/rag/intent.py` | 55 |
| `backend/app/services/rag/rewrite.py` | 136 |
| `backend/app/services/embedding.py` | 73 |
| `backend/app/services/vector_store.py` | 411 |
| `backend/app/services/rerank.py` | 103 |
| `backend/app/services/llm_ops/rerank.py` | 200 |
| `backend/app/services/llm_ops/base.py` | 42 |
| `backend/app/services/llm_ops/parsing.py` | 51 |
| `backend/app/services/llm_ops/__init__.py` | 25 |
| `backend/app/services/ner.py` | 87 |
| `backend/app/services/pipeline_ext/payload.py` | 79 |
| `backend/app/services/correction.py` | L1-260（向量段全文） |
| `backend/app/api/__init__.py` | 57 |
| `backend/app/db/models/content.py` | L1-70（列定义段） |

### 客户端（实读全文）
| 文件 | 行数 |
|---|---|
| `client/utils/search_api.uts` | 279 |
| `client/components/TabSearch/TabSearch.uvue` | 732 |

### 脚本 / 部署
| 文件 | 覆盖 |
|---|---|
| `scripts/build_image_index.py` | 全文 |
| `scripts/warm_hf_models.py` | 全文 |
| `scripts/eval_image_search.py` | L1-60 |
| `scripts/audit_harness_baseline.json` | 全文（本域 `dup_patterns` 计数取证） |
| `deploy/docker-compose.yml` | Qdrant 服务段（L104-153） |
| `deploy/.env.production.template` | QDRANT_* 段（L31-64） |
| `deploy/scripts/healthcheck.sh` | Qdrant 探活段（L21/95-100） |
| `deploy/scripts/bootstrap.sh` | L52/74-75 |
| `deploy/systemd/yishu-api.service` | L20 |
| `deploy/scripts/check_env_template.py` | L27 |

### 未读 / 未覆盖
- `backend/app/services/wechat/service.py`：仅实读 L360-410（`find_memories()` —— `rag_search` 的**第二生产消费方**），未逐行读全文（归域⑥/微信域）。
- `backend/app/services/external/dashscope.py`：仅实读 L55-195（`_chat_text`/`rewrite_query`/`route_query`/`image_caption`/`moderate`），未读全文（归外部依赖域）。
- `backend/app/core/config.py`：仅实读 rerank 与 rate_limit 段（L140-219），未读全文（归配置域）。
- `backend/app/services/embedding.py` 的 BGE-M3 实际数值行为（dense/sparse 输出）**未做运行时验证**（只读代码 + 常量），本域结论不含数值精度断言。
- `client/components/TabSearch/TabSearch.uvue` 的 ~300 行生成样式段：仅确认其为生成物，未逐行审计。
- `backend/tests/`：仅以 `Select-String` 取证（`test_rag.py` / `test_image_search.py` / `test_vector_store.py` / `test_search.py` / `test_external.py` 的 import 与断言行），未实读断言全文。
- **未执行任何运行时命令**（未跑 pytest / 未起 Qdrant）；所有结论均为静态代码证据。

### `git status --short`
```
?? .codebuddy/
?? .trae/specs/refactor-extensibility-maintainability-wave/audit/
```
**在途业务文件 = 0**。`AGENTS.md` 记录的「第四窗（媒体票据 Valet Key）主区 8 文件在途未提交＝勿碰名单」（`media.py` / `media_url.py` + events/config/errors/main/content-schema/storage）**当前已不在在途列表**（`recall.py:111` 已在调用 `content_urls`，说明该批已入库）。本域因此无「勿碰」冲突。

---

## 召回路径与降级矩阵（本域重点）

### A. 两条主链路的步骤对照

| 步骤 | 文本搜索 `search()` | 以图搜图 `search_by_image()` | 是否一致 |
|---|---|---|---|
| 入口 | `api/search.py:81` → `rag/__init__.py:82` | `api/search.py:61` → `rag/image.py:74` | — |
| 并发信号量 | `_search_semaphore`（`__init__.py:91`） | `_search_semaphore`（`image.py:85`） | ✅ 共用 |
| Query 改写 | `_rewrite_query(q)` → 时间/NER/LLM（`__init__.py:101`） | 无（查询是图片） | — |
| 用户隔离过滤 | `filters["user_id"]`（`__init__.py:105-106`） | `filters["user_id"]`（`image.py:119-120`） | ✅ |
| 意图路由 | `_route_query` → text/image（`__init__.py:109`） | 无（恒 image） | — |
| 类目路由 | `_classify_query_intent` → `content_class`（`__init__.py:122-125`） | **无** | ❌ |
| 编码 | `encode_query` → dense+sparse（`__init__.py:129`） | `encode_dense(caption)` 单塔（`image.py:115`） | ❌（设计如此） |
| 召回 | `store.search()` dense+sparse RRF（`vector_store.py:210-246`） | `store.search_image()` 单 image_vec（`vector_store.py:274-300`） | ❌ |
| mixed 双路融合 | `intent=="mixed"` 三分支（`__init__.py:135-141/149-154/163-168`） | 无 | ❌ **分支不可达**（见 D05-1） |
| NER 空结果回退 | 有（`__init__.py:146-157`） | 无 | ❌ |
| 类目空结果回退 | 有（`__init__.py:160-171`） | 无 | ❌ |
| 原查询双路召回 | 有（`__init__.py:176-183`） | 无 | ❌ |
| Qdrant 降级 | `_pg_fallback_search`（`__init__.py:184-189`） | **无兜底**（`image.py:122-126` → 空结果） | ❌ |
| 精确命中提升 | `_boost_exact_matches`（`__init__.py:192`） | 无 | ❌ |
| 溯源组装 | `_assemble_hits`（`recall.py:73`） | 同（`image.py:128`） | ✅ |
| Rerank 第一层（bge） | `rerank_auto_enabled()` 门控（`__init__.py:205-210`） | 无 | ❌ |
| Rerank 第二层（LLM） | `llm_rerank`（`__init__.py:218-232`） | 无 | ❌ |
| 规则级敏感过滤 | `filter_sensitive_rule`（`__init__.py:236-242`） | 同（`image.py:132-138`） | ✅ |

**结论**：两条链路共享「隔离/组装/敏感过滤/信号量」4 项，**分叉 9 项**；其中 `content_class`、两处空结果回退、原查询双路、rerank 两层属**文本链路独有能力**，以图搜图路径在同类场景下无对应兜底（见 D05-14）。

### B. 降级 vs 主路径结果形状对照

| 维度 | 主路径（Qdrant） | 降级路径（PG ILIKE） | 是否同形 |
|---|---|---|---|
| hit 键集 | `content_id/score/dense_score/sparse_score/text`（`vector_store.py:328-334`；image 路 `:293-299`） | 同 5 键 + `pg: True`（`pg_fallback.py:81-88`） | ✅ 同形（多 1 标记） |
| `trace.matched` | `["dense"]`/`["sparse"]`/两者（`recall.py:144-151`） | `["pg"]`（`recall.py:145-146`） | ✅ 契约兼容 |
| 分数口径 | RRF `0.7/(60+rank)+0.3/(60+rank)`（`vector_store.py:310,318`） | 合成 `1.0 - i*0.01`（`pg_fallback.py:83`） | ⚠️ **量纲不可比**（无排序含义，仅占位） |
| 用户隔离 | `filters["user_id"]`（`__init__.py:105-106`） | SQL `Content.user_id == user_id`（`pg_fallback.py:47`） | ✅ |
| 软删过滤 | `_assemble_hits` DB 回查 `deleted_at.is_(None)`（`recall.py:104`） | SQL 直带（`pg_fallback.py:48`）**且** `_assemble_hits` 二次回查 | ✅ 双重 |
| `status == "done"` 过滤 | 有（`recall.py:105`） | **无**（`pg_fallback.py:46-49`） | ❌ **缺**（见 D05-5） |
| `content_types` 别名 | `_to_filter` 展开 MatchAny（`vector_store.py:348-362`） | `CONTENT_TYPE_ALIASES` 归一（`pg_fallback.py:50-55`） | ✅ 同口径（**两份实现**） |
| NER / 类目空结果回退 | 有（`__init__.py:146-171`） | 无 | ❌ |
| 自身失败行为 | 抛异常 → 被 `except` 捕获（`__init__.py:184`） | 静默 `return []`（`pg_fallback.py:68-72`） | ❌ **空结果与失败不可区分**（见 D05-17） |
| `degraded` 标记 | `False` | `True`（`__init__.py:186`） | ✅ |
| 以图搜图降级 | — | **无 PG 兜底**：`degraded=True` + `hits=[]`（`image.py:122-126`） | ❌ |

**一句话结论**：**降级路径与主路径的 hit 形状一致（同 5 键 + `pg` 标记，`_assemble_hits` 可直接消化），但过滤口径与兜底层级不一致**——缺 `status=="done"`、缺 NER/类目回退、分数无排序含义、自身失败静默吞；且**以图搜图完全没有降级路径**，Qdrant 不可用时直接空结果。

---

## 条目

| # | 位置(文件:行) | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次建议 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| D05-1 | `backend/app/services/rag/__init__.py:135-141`, `:149-154`, `:163-168` | 死码（不可达分支） | P1 | 删掉 3 段不可达代码，`intent` 语义收敛 | 需确认是否有外部计划启用 mixed 路由 | 删码(需证死) | `Select-String 'intent =='` 仅 3 处 `"mixed"`；`intent.py:41-55` `_route_query` 仅 2 个 `return`（`"image"`/`"text"`） | 批次3 | 确认 |
| D05-2 | `backend/app/schemas/search.py:10` | 死码 + 契约漂移 | P2 | 请求契约不再声明一个从未被读的字段 | 需确认是否有外部调用方按文档传 `intent` | 删码(需证死) | `Select-String '\.intent\|"intent"'` → 全库无 `q.intent` 读取；客户端 `search_api.uts:236-243` 只发 `q/limit/content_types` | 批次4 | 确认 |
| D05-3 | `backend/app/services/vector_store.py:337-398` × `backend/app/services/rag/pg_fallback.py:50-66`；`backend/app/services/pipeline_ext/payload.py:72-77` | 真实重复逻辑 + **静默丢弃** | **P0** | 过滤维度单点化；未知过滤键不再被静默忽略 | 改动面广（过滤翻译 + payload 契约 + PG 兜底三处）；需回归全部过滤用例 | 行为等价替换 | `_to_filter` if/elif 覆盖恰好 7 键且**无 `else`**（`vector_store.py:347-397`）；`pg_fallback` 同 7 键另写一份（含重复的 `CONTENT_TYPE_ALIASES` import）；payload 写入 `payload.py:74-77` 4 字段 | 批次2 | 确认 |
| D05-4 | `backend/app/services/rag/recall.py:55` × `backend/app/services/rag/pg_fallback.py:39` | 真实重复逻辑 | P2 | 词元切分口径单点化（改一处即两侧同步） | 低 | 行为等价替换 | 两行正则字面量**逐字符相同**（`Select-String 're\.split\('` 全库仅 2 命中） | 批次4 | 确认 |
| D05-5 | `backend/app/services/rag/pg_fallback.py:46-49`, `:74-83`；`backend/app/services/rag/__init__.py:146-171` | 边界/一致性（降级口径分叉） | P1 | 降级召回条数与主路径同口径，`limit` 不再被静默浪费 | 需确认 degraded 场景是否要求与主路径等价条数 | 行为等价替换 | 主路径 DB 回查带 `status == "done"`（`recall.py:105`），PG 兜底 SQL **不带**（`pg_fallback.py:46-49`）→ 半态行被 SQL 捞出、再被 `_assemble_hits` 丢弃（`recall.py:142-143`）→ 有效条数 < limit | 批次2 | 确认 |
| D05-6 | `backend/app/services/rag/__init__.py:54-79` | 零调用导出（8 项） | P2 | `__all__` 恢复"对外契约"可信度 | 需确认无文档/外部脚本按名引用 | 删码(需证死) | 逐符号 `Select-String`：`SEARCH_CONCURRENCY` 仅 `recall.py:25-26` + re-export；`_CAPTION_CACHE_MAX`/`_CAPTION_CACHE_TTL_SECONDS`/`_caption_cache_lock` 仅 `image.py` 内部；`_CLASS_RULES` 仅 `intent.py:17,33`；`_TIME_PATTERNS` 仅 `rewrite.py:22,52`；`_search_by_image_impl` 仅 `image.py:86`；`_search_semaphore` re-export 无外部消费 | 批次4 | 确认 |
| D05-7 | `backend/app/services/llm_ops/base.py:27-32`；`backend/app/services/llm_ops/__init__.py:16,24` | 零调用导出（门面空转） | P2 | `llm_ops` 门面只保留真实被用的转发 | 需确认无域外调用方 | 删码(需证死) | `Select-String 'llm_ops.base'` 全部 import 行仅取 `chat_text`/`llm_available`/`moderate`；**无一处** import `rewrite_query`/`route_query`；`rag/rewrite.py:15` 反而直连 `external`（见 D05-13） | 批次4 | 确认 |
| D05-8 | `backend/app/services/rerank.py:49-76`, `:79-103`；`backend/app/core/config.py:168,176` | 门禁缺口 + 死路径（"关着的路不可测"） | P1 | 第一层 rerank 从"永不可达"变为可验证 | 启用后需 GPU + 模型；CPU 上 ~17-40s 超 P95 门禁 | 新增工具 | 全库 `rerank(` 调用点**仅** `rag/__init__.py:210`；测试只 import `rerank_auto_enabled`（`test_rag.py:795`），**无任何测试调用 `rerank()`**；`config.py:168 rerank_enabled=False`、`:176 rerank_auto_enable=False`；`backend/models/` **仅 README.md**（无模型目录）→ `_load_model()` 恒 `None`；`lru_cache(maxsize=1)` 把 `None` 永久缓存（`rerank.py:49`） | 批次2 | 确认 |
| D05-9 | `backend/app/core/config.py:161` × `backend/app/services/rerank.py:3-4`, `:57` × `scripts/warm_hf_models.py:23` | 声明漂移（模型名三处不一致） | P2 | 模型名单点化，排障不再三处对不上 | 低（当前无模型目录，不影响运行） | 行为等价替换 | `config.py:161 = "bge-reranker-v2-m3"`；`rerank.py:3,4` docstring 写 `bge-reranker-base`，`:57` 默认值 `"bge-reranker-base"`；`warm_hf_models.py:23` 写 `backend/models/bge-reranker-base` | 批次4 | 确认 |
| D05-10 | `backend/app/services/rag/__init__.py:232` × `client/utils/search_api.uts:104-135` | 死数据 + 端到端契约断裂 | P2 | LLM 精排理由要么展示、要么不写 | 低 | 行为等价替换 | 后端写入 `h.trace["llm_rerank_reason"]`（`__init__.py:232`）；`Select-String 'llm_rerank_reason'` 命中 `__init__.py:232` + `test_rag.py:820,826,848,849`，**客户端 0 命中**；`traceToText` 只读 `matched`/`dense_score`/`sparse_score` | 批次4 | 确认 |
| D05-11 | `client/utils/search_api.uts:151-166` × `:215-226` | 真实重复逻辑（10 参构造手抄两份） | P2 | hit 映射单点化，字段增删不再两处改 | 低 | 行为等价替换 | 两段均 `new SearchHitItem(...)` 传 10 个位置参数（构造器 `:36-47`）；`parseSearch` 走 `content_id/...`，`fetchRecentContents` 走 `id/...` 且 `score` 恒 0 | 批次4 | 确认 |
| D05-12 | `scripts/build_image_index.py:77,82,102,109,112`；`scripts/eval_image_search.py:4,43`；`backend/tests/test_image_search.py` ×14 行；`backend/tests/test_vector_store.py:5,60` | 硬编码魔法串 | P2 | 基准库名收敛为常量，避免与生产 `COLLECTION` 割裂 | 低 | 新增工具 | `Select-String 'yishu_benchmark'` = **26 处 / 6 文件**；生产侧有 `COLLECTION`（`vector_store.py:25`）+ `default_collection()`（`:44`），基准侧**无任何常量** | 批次4 | 确认 |
| D05-13 | `backend/app/services/rag/rewrite.py:15`；`backend/app/services/rag/image.py:44,52`；`backend/app/services/rag/recall.py:111`；`backend/app/services/rag/__init__.py:238`；`backend/app/services/rag/image.py:134` | 边界/依赖方向（越级直连 external） | P1 | `rag` 域回到"只经 llm_ops 门面触达 LLM"的单向边界 | 需为 `image_caption` 在 llm_ops 补转发位（门面覆盖缺口） | 纯移动+兼容再导出 | `llm_ops/base.py:1-7` 明文规则「各域 Agent 不得直接 import dashscope」；但 `rag/rewrite.py:15` 直连 `external.rewrite_query`（**`llm_ops.base.rewrite_query` 已存在且零调用**，见 D05-7）；`rag/image.py:44,52` 直连 `external.dashscope.image_caption`（llm_ops 无对应转发） | 批次3 | 确认 |
| D05-14 | `backend/app/services/rag/image.py:106-138` | 边界/功能缺口（路径能力不对称） | P1 | 以图搜图与文本搜索能力对等，降级不再"裸奔" | 加 rerank/回退会改变以图搜图延迟与结果集 | 行为等价替换 | `_search_by_image_impl` 内**无** `_classify_query_intent`、无两处空结果回退、无 `_boost_exact_matches`、无 `rerank_auto_enabled()`/`llm_rerank`；`except` 分支直接 `raw_hits = []`（`:126`）**无 PG 兜底** | 批次3 | 候审 |
| D05-15 | `backend/app/schemas/search.py:37,41`；`scripts/eval_image_search.py:4` vs `:42`；`backend/app/services/rag/__init__.py:6` | 声明漂移（注释/契约与实现不符） | P2 | 契约文档不再误导接入方 | 无 | 行为等价替换 | `SearchResult.intent` 注释写 `text / image / mixed`（`:37`）但 `_route_query` 只产 text/image；`degraded` 注释写「Qdrant 降级纯 PG 检索」（`:41`）但 image 降级也置 True 且无 PG；`eval_image_search.py:4` docstring 写 `content_types=[image]` 而 `:42` 用 `["photo"]`；`rag/__init__.py:6` 仍写「bge-reranker 粗排（占位：M1 后接）」但实现已在 `:205-210` | 批次4 | 确认 |
| D05-16 | `backend/app/services/correction.py:17-18`, `:34-50`, `:57-58` | 边界/抽象覆盖（绕过 VectorStore 自建第二套） | P1 | 向量库调用单点化，换库只改一处 | 需为 collection 抽象补齐接口（`VectorStore` 目前硬绑生产库语义） | 纯移动+兼容再导出 | `correction.py:17-18` 直接 import `QdrantClient` + `qdrant_client.http.models`；`:34-50` 自建 `_get_store()`（自行 `create_collection`）；`:57-58` 自建 `_point_id()`（与 `vector_store.point_id_for` 同构但独立）；`CORRECTION_COLLECTION`（`:33`）为独立 collection，`VectorStore` 无 collection 参数化抽象（仅 `search`/`search_image` 方法级 `collection` 形参） | 批次3 | 确认 |
| D05-17 | `backend/app/services/rag/pg_fallback.py:68-72` × `backend/app/services/rag/__init__.py:184-189` | 边界/异常（空结果 vs 失败不可区分） | P2 | 降级二次失败可被观测/上报 | 低 | 行为等价替换 | `_pg_fallback_search` 自身 `except` → `return []`（`:70-72`），**无任何失败信号**；调用方已先置 `degraded = True`（`:186`），故「PG 兜底也失败」与「PG 兜底正常但无命中」对客户端**完全同形**（`degraded=true` + `hits=[]`） | 批次4 | 确认 |

---

**D05-1 证据（P1 · `intent == "mixed"` 三分支不可达）**：
读了 `intent.py:41-55`，`_route_query` 函数体只有两条返回路径：`if any(h in q for h in image_hints): return "image"`（`:53-54`）与末尾 `return "text"`（`:55`）。docstring（`:42-47`）明确记录「2026-08-25 调研：LLM 路由实测有害……路由保持规则版」，即 mixed 已从设计上废弃。
读了 `rag/__init__.py:109-171`，`intent = _route_query(rewritten)`（`:109`）之后出现 `if intent == "image"`（`:110`）、`if settings.class_routing_enabled and intent == "text"`（`:122`）与**三处** `if intent == "mixed"`（`:135`、`:149`、`:163`）。
`Select-String 'intent =='` 全库取证：`"mixed"` 仅出现在上述 3 行；无任何写入 `intent = "mixed"` 的代码（`SearchResult.intent` 的赋值点 `__init__.py:247` 用的是同一局部变量）。故三段 mixed 融合逻辑（`_merge_recalls([image_filters 路, 全量路])`）为**不可达死码**。注意 `:176-181` 的原查询双路召回是**另一件事**（`rewritten != q.q` 门控，可达）。

**D05-2 证据（P2 · `SearchQuery.intent` 零消费）**：
读了 `schemas/search.py:10`：`intent: Literal["auto", "text", "image"] = "auto"`，注释写「auto=LLM 路由」。
`Select-String '\.intent|"intent"'`（backend/app + tests + scripts + client）命中：`rag/__init__.py:39,110,122,135,149,163`（均为**局部变量** `intent`，非 `q.intent`）、`test_image_search.py:167` 与 `test_rag.py:465`（`result.intent` 断言**响应**字段）、`test_search.py:67`（断言响应 JSON 的 `intent` 键）、`search_api.uts:81`（`this.intent` 为**响应**模型字段）、`uni_modules/.../index.uts` 与 `generate_test_photos.py`（Android `Intent`，同名无关）。
**无任何一处读取 `q.intent`**。客户端 `search_api.uts:236-243` 构造请求体只 set `q`/`limit`/`content_types`。故该字段为纯装饰：`auto`/`text`/`image` 三值均不改变行为。

**D05-3 证据（P0 · 过滤器翻译双实现 + 未知键静默丢弃）**：
读了 `vector_store.py:337-398`，`_to_filter` 为 `for key, value in filters.items()` + `if/elif` 链，分支恰为 7 个：`content_types`（`:348`）、`time_from`（`:363`）、`time_to`（`:368`）、`place`（`:373`）、`tag`（`:378`）、`content_class`（`:383`）、`user_id`（`:390`）。**链尾无 `else`**，直接 `return models.Filter(must=must) if must else None`（`:398`）——任何未列出的 key **被静默忽略**，不报错、不告警。
读了 `pg_fallback.py:50-66`，同一组 7 键**另写一份** `if filters.get(...)` 链，并**重复 import** `CONTENT_TYPE_ALIASES`（`:53`，`vector_store.py:39` 已有同名常量）。
读了 `pipeline_ext/payload.py:72-77`，`build_payload` 写入 `content_type`/`content_class`/`taken_at`/`user_id`，`extend_payload` 另补 `place`（`:40`）/`tags`（`:57,59`）——即**第三份**过滤维度清单。
后果：新增一个过滤维度（例如"按情绪过滤"或未来的隔离维度）需**协调改 ≥3 处**（`_to_filter` + `pg_fallback` + payload 写入）。任一处遗漏，`_to_filter` 侧是**静默丢弃**——若漏的是隔离类键（如 `user_id` 同族），将直接导致跨用户召回，属"易致事故"。当前 7 键全对齐，**无现存功能性缺陷**，故本项为结构性 P0（扩展风险），非现存 bug。

**D05-5 证据（P1 · 降级路径缺 `status=="done"` 与回退层级）**：
读了 `recall.py:97-107`：主路径 DB 回查条件为 `Content.id.in_(valid_ids)` + `Content.user_id == user_id` + `Content.deleted_at.is_(None)` + **`Content.status == "done"`**（`:105`）。
读了 `pg_fallback.py:46-49`：SQL 仅 `Content.user_id == user_id` + `Content.deleted_at.is_(None)`，**无 status 条件**。
读了 `recall.py:138-143`：`if c is None and rh["content_id"] in attempted_ids: continue` —— 即 PG 兜底捞出的 `processing`/`failed` 行会在组装层被整条剔除。
故降级路径的**有效条数 < limit**（SQL 捞了半态行、组装层丢了），用户看到的结果比 `limit` 少且原因不可见。此外主路径有两级空结果回退（NER `__init__.py:146-157`、类目 `:160-171`）与分数梯度，降级路径均无。

**D05-6 证据（P2 · 8 项零调用再导出）**：
逐符号 `Select-String` 全库取证（排除 `__pycache__`/`unpackage`）：
- `SEARCH_CONCURRENCY`：`recall.py:25`（定义）、`:26`（同文件使用）、`__init__.py:42`（import）、`:55`（`__all__`）——**无包外消费者**。
- `_search_semaphore`：`recall.py:26`（定义）、`__init__.py:91`、`image.py:85`（**包内真实使用**）、`__init__.py:46,66`（import/`__all__`）——符号存活，但 **re-export 无包外消费者**。
- `_search_by_image_impl`：`image.py:89`（定义）、`:86`（包内调用）、`__init__.py:36,78`——re-export 零消费。
- `_CAPTION_CACHE_MAX`（`image.py:22,66,68`）、`_CAPTION_CACHE_TTL_SECONDS`（`image.py:23,50`）、`_caption_cache_lock`（`image.py:25,48,65`）、`_CLASS_RULES`（`intent.py:17,33`）、`_TIME_PATTERNS`（`rewrite.py:22,52`）——均**仅定义文件内部使用** + `__init__.py` 的 import/`__all__` 行。
- 对照组（有外部消费者，**非**死导出）：`_cached_image_caption`（`test_image_search.py` ×6、`check_dashscope_matrix.py` ×3）、`_caption_cache`（测试 ×8、脚本 ×7）。

**D05-7 证据（P2 · `llm_ops` 门面两函数空转）**：
读了 `llm_ops/base.py:27-32`：`rewrite_query`（`:27-28`）与 `route_query`（`:31-32`）仅转发 `dashscope`。
`Select-String 'route_query'` 全库：定义处 `base.py:31`、re-export `llm_ops/__init__.py:16,24`、`external/__init__.py:11,14`、`dashscope.py:101`；**调用点**只有 `tests/test_external.py:30`（测 `external.route_query` 抛错）与 `scripts/check_dashscope_matrix.py:85`（直连 `external.dashscope`，`scripts/check_dashscope_matrix.py:79`）——**均不经 `llm_ops.base`**。
`Select-String 'from app.services.llm_ops'` 全部 import 行取值集合 = `{chat_text, llm_available, moderate, annotate, merge_verdict, guard_managed, event_merge, parsing, rerank}` —— **不含 `rewrite_query`/`route_query`**。故 `llm_ops.base` 的这两个转发函数**零调用**；它们本应是 `rag/rewrite.py:15` 的入口（见 D05-13）。

**D05-8 证据（P1 · 第一层 bge rerank 不可达且零测试）**：
读了 `rerank.py:27-46`，`rerank_auto_enabled()` 三条早退：`settings.rerank_enabled` 真→True（`:35-36`）；`rerank_auto_enable` 假→False（`:37-38`）；`_gpu_available()` 假→False（`:39-41`）；`_load_model() is None`→False（`:42-44`）。
读了 `config.py:168`（`rerank_enabled: bool = False`）与 `:176`（`rerank_auto_enable: bool = False`）→ **两个开关默认均为假**，函数恒返回 `False`。
`Get-ChildItem backend/models` → **仅 `README.md`**（无 `bge-reranker-*` 目录）→ `_load_model()` 的 `(model_path / "config.json").exists()` 判假 → `return None`（`rerank.py:60-61`）；且该函数带 `@lru_cache(maxsize=1)`（`:49`），**一旦首次返回 None 即进程级永久缓存**，即便随后放入模型目录也不会重新加载。
`Select-String 'rerank\('` 全库调用点：**仅** `rag/__init__.py:210`。测试侧 `test_rag.py:792-811` 只测 `rerank_auto_enabled()` 的策略分支（`:795` import），**无任何测试调用 `rerank()`** —— 即"关着的路"完全落在测试覆盖之外，将来把开关打开时该路径**从未被执行过**。

**D05-11 证据（P2 · 客户端 hit 映射手抄两份）**：
读了 `search_api.uts:36-58`，`SearchHitItem` 构造器为 10 个**位置参数**（`contentId, contentType, text, takenAt, place, eventId, eventTitle, score, trace, thumbnailUrl`）。
`:151-166`（`parseSearch`）与 `:215-226`（`fetchRecentContents`）各自 `new SearchHitItem(...)` 传满 10 个位置参数：前者读 `content_id/content_type/text/taken_at/place/event_id/event_title/score/trace/thumbnail_url`；后者读 `id/content_type/text/taken_at|created_at/place/''/''/0/''/thumbnail_url`。
两段字段语义不同（`id` vs `content_id`、`score` 恒 0、事件字段留空），但**构造器签名与字段顺序是同一份隐式契约**——新增字段需同时改构造器 + 两处调用点。

**D05-13 证据（P1 · `rag` 越级直连 `external`）**：
读了 `llm_ops/base.py:1-7`，明文规则：「各域 Agent **不得直接 import dashscope**」，例外仅 `llm_ops/moderate.py`（`:5-7`）。
实际命中：`rag/rewrite.py:15` `from app.services.external import rewrite_query`（**`llm_ops.base.rewrite_query` 已存在，见 D05-7**，即存在可用门面却被绕过）；`rag/image.py:44,52` `from app.services.external.dashscope import image_caption as _vl_caption`（llm_ops **无** `image_caption` 转发 → 门面覆盖缺口，属结构性而非纯纪律问题）；`rag/recall.py:111` `media_url.content_urls`、`rag/__init__.py:238` 与 `rag/image.py:134` `sensitive_words.filter_sensitive_rule`（后三者不属 LLM 调用，规则字面只约束 dashscope，故仅登记为"域直连 external 面较宽"）。

**D05-14 证据（P1 · 以图搜图路径能力不对称）**：
读了 `image.py:106-138` 全文，`_search_by_image_impl` 步骤为：`_cached_image_caption`（`:112`）→ `encode_dense`（`:115`）→ `store.search_image`（`:121`）→ `_assemble_hits`（`:128`）→ `filter_sensitive_rule`（`:132-138`）。
对照文本路径 `__init__.py:95-242`，**缺失**：`_classify_query_intent` 类目路由（`:122-125`）、NER 空结果回退（`:146-157`）、类目空结果回退（`:160-171`）、原查询双路召回（`:176-183`）、`_boost_exact_matches`（`:192`）、`rerank_auto_enabled` 第一层（`:205-210`）、`llm_rerank` 第二层（`:218-232`）。
`:122-126` 的 `except` 分支为 `degraded = True; caption = ""; raw_hits = []` —— **无 PG 兜底**，与文本路径 `__init__.py:184-189` 的 `_pg_fallback_search` 不对称。故 Qdrant 不可用时，文本搜索仍有关键词结果，以图搜图直接空。
标 `候审`：是否应补齐需产品确认（以图搜图加 rerank 会显著抬高延迟，可能是有意取舍）。

**D05-16 证据（P1 · `correction.py` 自建第二套 Qdrant 实现）**：
读了 `correction.py:17-18`：直接 `from qdrant_client import QdrantClient` + `from qdrant_client.http import models`（**绕过 `vector_store` 的 `get_qdrant_client` 之外的封装**）。
`:34-50` `_get_store()`：自建 `create_collection`（列 `get_collections()` 判存在性 → 建 `text_vec`），仅复用 `get_qdrant_client()` 单例连接（注释自称 P2-04 收敛）。
`:57-58` `_point_id()`：`uuid5(NAMESPACE_URL, f"{user_id}:{content_id}")` —— 与 `vector_store.point_id_for`（`vector_store.py:92-98`，`uuid5(NAMESPACE_URL, content_id)`）**同构但独立实现**，且语义不同（带 user 前缀），无法直接复用。
`:33` `CORRECTION_COLLECTION = "corrections"` 为**第二个 collection**，`VectorStore` 类只服务 `text_vec`/`image_vec`/`text_sparse` 的生产语义，**无 collection 抽象**（仅方法级 `collection` 形参，见 `vector_store.py:216,253,279`）。故换向量库时 `correction.py` 需整套重写。

---

## 依赖方向与抽象覆盖度结论

### FF1：本域是否存在反向依赖？

**`rag` 包与底层能力包之间为单向，无环；但存在 2 处越级/绕过（D05-13、D05-16）。**

- `rag → {embedding, vector_store, rerank, llm_ops, ner, schemas, db.models, external}`：正向 ✅（`rag/__init__.py:27-50`、`image.py:15-17`、`recall.py:15-18`、`pg_fallback.py:15-16`、`rewrite.py:13-16`、`intent.py` 无外部依赖）
- `vector_store` / `embedding` / `rerank` / `llm_ops` / `ner` → `rag`：**零命中**（`Select-String` 全库）→ **无环 ✅**
- `vector_store` 反向依赖检查：`vector_store.py:21` 只 import `app.core.config`；**未** import `rag`/`embedding`/`llm_ops` ✅
- `rag` 包内单向链：`intent`/`rewrite`/`pg_fallback` 无同包依赖；`image` → `recall`（`image.py:16`，取 `_assemble_hits`/`_search_semaphore`）；`__init__` → 全部子模块 ✅ 无环
- ❌ **越级 1**：`rag/rewrite.py:15` 直连 `services.external`，绕过 `llm_ops.base`（而 `llm_ops.base.rewrite_query` 已存在且零调用 → 门面被架空）
- ❌ **越级 2**：`rag/image.py:44,52` 直连 `external.dashscope.image_caption`（llm_ops **无**对应转发 → 门面覆盖缺口，属结构缺口非纪律）
- ⚠️ **绕过抽象**：`correction.py:17-18,34-50,57-58` 直连 `qdrant_client` 自建第二套向量库实现（不经 `VectorStore`）

### FF2-1：新增一条召回通道需改几处？（注册点全列）

**无任何注册表/插件机制，全部为硬编码列举。新增一路召回（如"标签召回""时间邻近召回"）需改动 8 处：**

1. `backend/app/services/vector_store.py:210-246` —— `VectorStore.search()` 目前**硬编码两路**（dense `:224-230` + sparse `:235-244`），无通道列表
2. `backend/app/services/vector_store.py:302-335` —— `_rrf_fuse()` 硬编码权重 `0.7`/`0.3`（`:310`/`:318`）与 `k=60`（`:303`），通道数变化即需改公式
3. `backend/app/services/vector_store.py:274-300` —— `search_image()` 是**独立方法**而非通道，第三路需再写一个方法
4. `backend/app/services/rag/__init__.py:138-141` / `:149-154` / `:163-168` / `:176-181` —— **4 个调用点**各自手写 `_merge_recalls([...])` 组合
5. `backend/app/services/rag/recall.py:29-37` —— `_merge_recalls()` 按 `content_id` 去重取最高分，**无通道来源记录**（无法区分"哪一路召回"）
6. `backend/app/schemas/search.py:31` —— `trace` 为无 schema 的 `dict`，通道名无契约
7. `client/utils/search_api.uts:104-135` —— `traceToText()` 硬编码白名单 `dense`/`sparse`（`:115-118`），其余原样透传（`pg` 会直接显示英文）
8. `backend/app/services/rag/__init__.py:205-210` / `:218-232` —— 两层 rerank 的候选构造硬绑 `hits`，通道变化需同步

### FF2-2：替换向量数据库需改几处？（注册点全列）

**`VectorStore` 只封装了生产库语义，无 provider 抽象。换库（如 Qdrant → Milvus/pgvector）需改动：**

**核心实现（1 个文件全量重写）**
1. `backend/app/services/vector_store.py` —— 全文（`_ensure_collection`/`_upsert_merged`/`update_payload`/`upsert_content`/`search`/`_rrf_fuse`/`upsert_image_vec`/`search_image`/`_to_filter`）
2. `backend/app/services/vector_store.py:401-411` —— 两个 `lru_cache` 单例 `get_store()` / `get_qdrant_client()`
3. `backend/app/services/vector_store.py:44-89` —— `default_collection()` / `test_collection_name()` / `is_test_collection()` / `cleanup_test_collections()`（依赖 `QdrantClient.get_collections/delete_collection` 专有 API）

**绕过封装、需独立重写**
4. `backend/app/services/correction.py:17-18,34-50,57-58,139-148` —— 直连 `qdrant_client` 的第二套实现（见 D05-16）

**配置**
5. `backend/app/core/config.py:40` —— `qdrant_url: str = "http://localhost:6333"`（**无 provider 字段**）

**数据模型（厂商命名列）**
6. `backend/app/db/models/content.py:60-61,91` —— `qdrant_text_id` / `qdrant_image_id` / `qdrant_point_id` 三个**以厂商命名**的列（换库需改列名或留历史包袱）

**测试**
7. `backend/tests/test_vector_store.py:5,60`、`backend/tests/test_image_search.py`（14 行）、`backend/conftest.py`（QDRANT_COLLECTION fixture）、`backend/tests/polling.py` —— 依赖 collection 名与客户端探活
8. `scripts/api_smoke_cases.py`、`scripts/test_agent.py` —— 依赖 Qdrant 健康检查与 collection 清理

**脚本**
9. `scripts/build_image_index.py:77-112`（`ensure_collection`/`upsert_content`/`upsert_image_vec` 调用面）、`scripts/eval_image_search.py:43`

**部署（5 个文件）**
10. `deploy/docker-compose.yml:104-153`（`qdrant` 服务 + `QDRANT_IMAGE`/`QDRANT_HTTP_PORT`/`QDRANT_GRPC_PORT`/`QDRANT_MEM_LIMIT` + healthcheck `:145`）
11. `deploy/.env.production.template:31-32,36,39,42,64`（`QDRANT_URL` 等 5 项）
12. `deploy/scripts/healthcheck.sh:21,95-100`（`/healthz` REST 探活，**Qdrant 专有端点**）
13. `deploy/scripts/bootstrap.sh:52,74-75`
14. `deploy/systemd/yishu-api.service:20`（`QDRANT_URL` 注入）、`deploy/scripts/check_env_template.py:27`（`QDRANT_` 前缀豁免）

**关键结论**：换向量库需穿透 **14 处（含 1 个全量重写文件 + 1 个绕过封装的第二实现 + 3 个厂商命名 DB 列 + 5 个部署件）**，**无 provider 接口、无单点扩展位**。`_rrf_fuse` 的 `0.7/0.3/k=60` 与 `VECTOR_SIZE=1024`（`vector_store.py:31`）亦为硬编码，未进配置。

---

## 本域已查无问题项（避免遗漏被误读）

1. **`vector_store` 不反向依赖 `rag`/`embedding`/`llm_ops`** —— `vector_store.py:18-21` 仅 import `qdrant_client` 与 `app.core.config`，分层干净；`rag` 包与底层包之间**无循环依赖**（`Select-String` 零命中）。
2. **用户隔离在两条主链路的召回阶段均已生效** —— `__init__.py:105-106`（文本）与 `image.py:119-120`（以图搜图）均写 `filters["user_id"]`，且 `_to_filter:390-397` 有对应分支；`_assemble_hits:100` 另做 DB 回查二次隔离（纵深）。
3. **D3 软删闸门（2026-09-08 修复）在组装层正确落地** —— `recall.py:97-107` 回查带 `deleted_at.is_(None)` + `status == "done"`，`:138-143` 对合法 UUID 且回查落空者**整条剔除**（`attempted_ids` 机制），非 UUID 测试点（`rag-001` 类）照常放行——设计正确且有注释记录真机实证。
4. **`_boost_exact_matches` 的原地修改风险已修** —— `recall.py:58-61` 显式 `nh = dict(h)` 拷贝后改 score，注释记录「原地改 score 会污染调用方复用的列表」的测试暴露历史。
5. **时间表达"仅句首生效"的修复正确** —— `rewrite.py:52-54` `if m and m.start() == 0`，配 `_TIME_PATTERNS` 长模式在前（`:22-33`），避免"把上个月的总结交了"被误判为时间意图。
6. **`_to_filter` 的 datetime → epoch 转换与 payload 写入口径一致** —— `vector_store.py:363-372` 用 `value.timestamp()`（Range gte/lte），`payload.py:72` 写 `int(content.taken_at.timestamp())`，两端互斥于 ISO 字符串（注释已声明），无类型漂移。
7. **content_type 别名双向兼容实现正确** —— `vector_store.py:348-362` 展开 `MatchAny([norm, "image"])`，`pg_fallback.py:50-55` 用 `CONTENT_TYPE_ALIASES` 归一，FIX-1 口径两端一致（重复实现见 D05-3，但语义正确）。
8. **`llm_rerank` 的"只改序不增删"约束成立** —— `__init__.py:226-232` 仅当 `rerank_reason` 存在时才替换 `hits`，且 `llm_ops/rerank.py` 内部自门控（无 key/mock/解析失败 → 原序）；`judged` 字典门控防 mock 污染排序。
9. **规则级敏感过滤在两条链路均落地且失败不阻断** —— `__init__.py:236-242` 与 `image.py:132-138` 同款，`except` 仅 `logger.warning`（fail-open 是有意的：转述内容的最小兜底）。
10. **`_cached_image_caption` 的过期缓存兜底与空 caption 不落缓存设计正确** —— `image.py:54-64` 失败优先返回 stale、成功但空描述也返回 stale，`:62-64` 空串不写缓存（不污染），`:66-70` 淘汰策略（清最早一半）零依赖可用。
11. **`api/search.py` 查询图安全纵深完整** —— 空文件校验（`:48-49`）、大小上限 10MB（`:50-51`）、**魔数嗅探** `is_photo_bytes`（`:54-55`）、后缀白名单防路径穿越（`:27-33`）、临时文件 `finally` 清理（`:60-63`）——四层防御齐备。
12. **并发信号量设计正确且被两入口共用** —— `recall.py:25-26` `BoundedSemaphore(4)`，`__init__.py:91` 与 `image.py:85` 均包裹实现体，防 BGE-M3/reranker 同时加载打满内存（注释记录 P2-01 动机）。
13. **测试隔离机制（TD-P1C）落地完整** —— `vector_store.py:44-89` `default_collection()` 每次实时读 `QDRANT_COLLECTION` 环境变量（非模块常量），`cleanup_test_collections()` 尽力清理且失败不阻断门禁。
14. **`client/components/TabSearch/TabSearch.uvue` 无重复页面** —— `client/pages/` 下**不存在** `pages/search/`（目录核实），732 行未超 `audit_harness_baseline.json` 的 client 阈值 800，故该文件是搜索页唯一实现、无历史副本分叉。
15. **`search_api.uts` 的隐私纪律正确** —— `:247` 面包屑只记 `q_len`、`:266` 只记 `limit`，**从不记用户查询原文/文件路径**（注释明确理由：面包屑发往 Sentry 外部服务）；`degraded` 态用 `captureMessage`（`:255`）覆盖"非异常但用户可感"的空档。

---

## 报告元信息
- 审计者：域⑤ 代码审计子 agent（易扩展/易维护重构波 · Phase A）
- 条目总数：**17**（P0 ×1、P1 ×6、P2 ×10）
- 降级路径 vs 主路径：**hit 形状同形（同 5 键 + `pg` 标记）但过滤口径与兜底层级不一致**（缺 `status=="done"`、缺 NER/类目回退、分数无排序含义、自身失败静默吞；以图搜图**无降级路径**）
- 依赖方向：**无环**；越级/绕过 **3 处**（D05-13 ×2、D05-16 ×1）
- 扩展点计数：新增召回通道 **8 处**；替换向量库 **14 处**（含 1 文件全量重写 + 3 厂商命名 DB 列 + 5 部署件）
- 状态：确认 **16** / 候审 **1**（D05-14 是否补齐以图搜图能力需产品确认）
- 方法学声明：本域**未执行任何运行时命令**（未跑 pytest、未起 Qdrant），全部结论为静态代码 + `Select-String` 取证
