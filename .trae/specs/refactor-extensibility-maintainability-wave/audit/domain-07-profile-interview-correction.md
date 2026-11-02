# 功能域⑦ 审计报告：画像 / 访谈 / 纠错

> 审计波次：「易扩展/易维护重构波」Phase A —— 逐功能域深度代码审计
> 审计对象：用户画像 B1、访谈式补全（B1-7 冷启动三问）、AI 纠错闭环（B5-c 三层裁决）、画像级敏感（B5b FIX-4）
> 审计基线：分支 `develop`，HEAD = `cec3172`（`git log --oneline -6` 实取）
> 审计纪律：**只读业务代码，未修改任何业务文件**；本波性质为严格行为等价，缺陷**只登记、不修**
> 范围：`backend/app/` + `client/` + `scripts/` + `deploy/`；**`agent/` 目录明确排除**
> 状态口径：`确认` = 有直接代码证据；`候审` = 启发式推测、需人工确认
> 严重度口径：P0 = 阻碍改动/易致事故；P1 = 明确结构债；P2 = 顺手可清
> 可回滚性枚举：`纯移动+兼容再导出` / `行为等价替换` / `新增工具` / `删码(需证死)`

---

## 1. 覆盖范围与实读清单

### 1.1 在途文件核实（审计开始时）

```
$ git status --short
?? .codebuddy/
?? .trae/specs/refactor-extensibility-maintainability-wave/audit/

$ git rev-parse --abbrev-ref HEAD
develop
```

**结论**：本域无在途修改的业务文件，工作树干净，仅有 2 个未跟踪目录（均非业务代码）。审计期间未产生任何业务文件改动。

> 工具环境注记：本环境 `Grep` 工具（ripgrep 后端）持续报 `rg execution error: program not found`，全部内容检索改用 PowerShell `Select-String` / `Get-ChildItem -Recurse | Select-String` 完成。下文所有"检索证据"均标注实际命令。

### 1.2 后端实读清单

| 文件 | 行数 | 读法 | 说明 |
|---|---|---|---|
| `backend/app/api/interview.py` | 51 | 全文 | 三端点（questions/answers/profile），均需鉴权 |
| `backend/app/api/corrections.py` | 78 | 全文 | 纠错入口；**D-22 回写修复落点 L64-69** |
| `backend/app/api/classify.py` | 138 | 全文 | arbitrate 已迁入 /classify 域；L89-110 裁决入队 |
| `backend/app/api/contents.py` | 902 | L450-530 | `profile_sensitive_router` 三端点（L466/484/501）；router 定义 L89 |
| `backend/app/services/interview.py` | 217 | 全文 | 冷启动访谈服务；硬编码关键词表 L49-84 |
| `backend/app/services/correction.py` | 343 | 全文 | 三层裁决；`arbitrate` L257-289（D-22 透传） |
| `backend/app/services/classifier.py` | 137 | 全文 | SetFit；`_fallback_result` L64-76（`degraded:True`） |
| `backend/app/services/profile_annotator.py` | 374 | 全文 | 画像标注核心（阈值/池/归一/更新/证据/历史） |
| `backend/app/services/profile_schema.py` | 169 | 全文 | 枚举集 JSON 加载器；`validate` L91-111 |
| `backend/app/services/echo.py` | 295 | L1-180 | `profile_sensitive` CRUD + 双查；`_profile_hit` L59-70 |
| `backend/app/services/pipeline.py` | — | L100-170 | `_classify_content` L124-132（**D-22 同族未修点**） |
| `backend/app/services/pipeline_ext/profile.py` | 33 | 全文 | 画像标注钩子（全异常吞掉，fail-safe） |
| `backend/app/services/pipeline_ext/sensitive.py` | 176 | 全文 | 事件级敏感钩子（≥3 次提及降级） |
| `backend/app/services/llm_ops/annotate.py` | 138 | 全文 | 画像枚举标注（LLM + mock 双通道） |
| `backend/app/services/export.py` | ~200 | 全文 | `_profile_out` / `_correction_out` 导出 |
| `backend/app/db/models/profile.py` | 125 | 全文 | 5 张画像域表 ORM |
| `backend/app/db/session.py` | 34 | 全文 | `get_db` 只 close 不 rollback |
| `backend/app/schemas/correction.py` | 36 | 全文 | `ArbitrateRequest` 契约增量 L29-36 |
| `backend/app/schemas/interview.py` | 56 | 全文 | `ANSWER_KEYS` 白名单 + 2000 字上限 |
| `backend/app/schemas/classify.py` | 56 | 全文 | `ClassifyResult.degraded` L24-25 |
| `backend/app/schemas/content.py` | — | L68-95 | `ProfileSensitiveCreate/Out`（含 `locked`） |
| `backend/app/main.py` | 153 | 全文 | 路由注册 L118-142（L120 profile_sensitive / L129 corrections / L135 interview） |
| `backend/tests/test_correction.py` | 285 | L225-295 | D-22 守护测试（degraded 透传 / active 回写 / 软删拒绝） |
| `backend/tests/test_interview.py` | 188 | L1-80 | 三问 keys / L0 激活 / L1 ≥5 维 / 复述确认 |
| `backend/migrations/versions/431bcaa8bd54_baseline_from_orm.py` | — | L665-672 | `profile_l2_evidence` 建表（无唯一约束） |
| `backend/migrations/versions/a7b8c9d0e1f2_add_p1b_perf_indexes.py` | — | L29 | `idx_l2_evidence_user_dim` 普通索引 |
| `backend/sql/schema.sql` | — | L247-254 | 同上（SQL 侧口径） |

### 1.3 客户端实读清单

| 文件 | 行数 | 读法 | 说明 |
|---|---|---|---|
| `client/pages/interview/interview.uvue` | 655 | 全文 | 冷启动访谈页；`fallbackQuestions` L85-89；`confirmation` L117 |
| `client/pages/portrait/manage.uvue` | 849 | L1-60, L140-462 | 画像管理页；`loadProfile` L222-255；敏感话题 L307-390 |
| `client/pages/portrait/theme-detail.uvue` | 429 | L1-45 | 主题详情（与本域关联弱） |
| `client/components/PortraitPrivacyPanel/PortraitPrivacyPanel.uvue` | — | L60-105 | `onClearPortrait` L82-100 |
| `client/components/RecordSheet/RecordSheet.uvue` | — | L700-745, L955-995 | `submitCorrection` 调用点 L978/L1328；自定义标签白名单 L709 |
| `client/utils/play.uts` | 612 | L105-212 | 访谈三函数 + `flattenDimensions` L155-172 |
| `client/utils/text_recorder.uts` | ~216 | L160-216 | `parseClassifyResult` L171-186；`submitCorrection` L189-203 |
| `client/utils/event_ops.uts` | 466 | 全文（检索） | 事件手动操作；**检索证实与本域零耦合**（见 §6） |
| `client/utils/contract.uts` | — | L39-51 | `PATH_CORRECTIONS` / `PATH_INTERVIEW_*` |

### 1.4 部署与脚本实读清单

| 文件 | 行数 | 说明 |
|---|---|---|
| `deploy/Dockerfile.backend` | 49 | `COPY backend/ ./` L39（**未复制 `docs/`**） |
| `deploy/docker-compose.yml` | — | backend volumes L156-159（仅挂 models + hf-cache）；`archive_mode` L56 默认 off |
| `deploy/.env.production.template` | — | 无 `PROFILE_ENUM_DIR`（全仓检索仅 2 处命中，均在 `profile_schema.py` 自身） |
| `deploy/scripts/backup_pg.sh` | 104 | 每日 03:30 `pg_dump -Fc` 全库 + 非空校验 + 轮转；WAL 说明 L16-19 |
| `backend/scripts/reflow_global.py` | — | L114-119 调 `mark_global_candidates`（第③层唯一触发入口） |
| `backend/scripts/measure_correction_gain.py` | — | L85-91 调 `arbitrate` 做纠错增益度量 |

---

## 2. 条目表

**总计 23 条：P0 = 1，P1 = 15，P2 = 7。全部状态 = `确认`（均有直接代码证据），无 `候审` 条目。**

| 编号 | 位置（文件:行） | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次建议 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| D07-1 | `deploy/Dockerfile.backend:39` + `deploy/docker-compose.yml:156-159` + `deploy/.env.production.template` + `backend/app/services/profile_schema.py:21-22,115` | 边界/部署缺口 | **P0** | 修后容器内画像/访谈/标注链路可用 | 不修则容器内首次 `get_schema()` 抛 `FileNotFoundError` | 新增工具 | 构建镜像后容器内 `python -c "from app.services.profile_schema import get_schema; get_schema()"` | 独立批次（部署配置，非本波重构范围） | 确认 |
| D07-2 | `backend/app/services/interview.py:49-84` vs `docs/画像维度枚举集_l0.json`（`aliases`） | 真实重复逻辑 | P1 | 收敛为单一真值源，消除同句双口径 | 收敛需保持现有命中集合（含双方独有项） | 行为等价替换 | `pytest backend/tests/test_interview.py`（现网断言仅覆盖部分键）+ 对照 `_mock_annotate` 输出 | 批次1（真值收敛） | 确认 |
| D07-3 | `backend/app/services/pipeline.py:124-132` | D-22 同族未修点① | P1 | 入库链不再把降级 mixed 写成权威分类 | 改动影响入库主链路，需回归 `test_pipeline*` | 行为等价替换 | `test_correction.py::test_arbitrate_degraded_passthrough` 同构测试 + 新增 pipeline 断言 | 批次1（与 D07-4/5/6 同批） | 确认 |
| D07-4 | `backend/app/api/corrections.py:64-69`（未改 `class_source`；未重索引 Qdrant） | D-22 同族未修点④ | P1 | 溯源字段与检索 payload 不再停旧值 | 重索引需处理失败回滚 | 行为等价替换 | 写纠错后查 `contents.class_source` 与 Qdrant payload `content_class` | 批次1 | 确认 |
| D07-5 | `backend/app/schemas/correction.py:29-36` + `backend/app/api/classify.py:100-109` + `backend/app/services/correction.py:292` | D-22 同族未修点③ | P1 | 契约增量不再静默丢弃 | 需定义层①/层②消费语义，勿引入"自动覆盖用户所选" | 新增工具 | 传 `content_id`/`preferred_label` 后断言 job 入参含该字段 | 批次1 | 确认 |
| D07-6 | `client/utils/text_recorder.uts:171-186`（`ClassifyResult` 类亦无该字段） | D-22 同族未修点② | P1 | 客户端 DTO 与后端契约对齐 | 当前无消费方（arbitrate 客户端路径已删），改动面窄 | 行为等价替换 | 类型检查 + 契约字段对照 | 批次1 | 确认 |
| D07-7 | `client/pages/portrait/manage.uvue:236-249` | 伪造数据/展示缺陷 | P1 | 停止向用户展示英文维度 id 与编造置信度 | 需后端补 label/confidence 真值，属产品口径变更 | 行为等价替换 | 真机打开画像页，观察标签名与置信度 | 批次2（需产品确认展示口径） | 确认 |
| D07-8 | `client/pages/interview/interview.uvue:85-89` | 语义错配/画像污染 | P1 | API 失败时不再把无关答案映射到 L0 维度 | 兜底文案需产品定稿 | 行为等价替换 | 断网/桩失败后作答，查 `profile_dimension_pending` 与 dimensions | 批次2 | 确认 |
| D07-9 | `client/pages/interview/interview.uvue:117,280-282` vs 模板 `L28-34` | 死状态/功能未接线 | P1 | 接上 B1-7 复述确认闭环（后端已产出） | 需产品确认交互形态（B1-6 对话式修改） | 行为等价替换 | 提交三问后断言页面渲染 `confirmation` 文本 | 批次2 | 确认 |
| D07-10 | `client/components/PortraitPrivacyPanel/PortraitPrivacyPanel.uvue:82-100` | 隐私安慰剂 | P1 | 消除"已删除全部画像"的误导性承诺 | 需后端画像清除端点（当前未登记） | 新增工具 | 清除后查服务端 `user_profile.dimensions` 是否仍非空 | 批次2（依赖后端新端点） | 确认 |
| D07-11 | `backend/app/services/echo.py:59-70,103-111` + `backend/app/db/models/profile.py:83` | 死字段/铁律未落实 | P1 | `locked` 获得真实语义；用户锁定不可被静默解除 | 改变 upsert 覆写语义需回归 `test_echo.py` | 行为等价替换 | `test_echo.py:243-244,307` 扩展断言 | 批次3 | 确认 |
| D07-12 | 全后端检索：`backend/app/api/` 无画像写端点（`services/interview.py:14` 声称"B1-6 后续"） | 「用户操作优先」无载体 | P1 | 画像主数据获得人工确认/修改/锁定能力 | 新增端点=新增能力，非等价重构；须产品先拍板 | 新增工具 | 端点存在性 + 覆盖测试 | 批次3（须先 grill 产品） | 确认 |
| D07-13 | `backend/app/services/profile_schema.py:107-110` | 硬编码枚举/注册点 | P1 | 新增维度不再改代码 | 移除校验会失去维度数回归护栏，需替代机制 | 行为等价替换 | 改 JSON 维度数后跑 `validate()` | 批次1 | 确认 |
| D07-14 | `backend/app/services/profile_annotator.py:335-351` + `client/utils/play.uts:155-172` + `backend/app/services/export.py:_profile_out` | 真实重复逻辑 | P1 | 展示口径单一化；客户端不再泄漏英文 dim id | 客户端摘要格式变更影响 UI 文案 | 行为等价替换 | 三处同输入比对输出 | 批次2 | 确认 |
| D07-15 | `backend/app/services/classifier.py:27-32` + `backend/app/api/corrections.py:36` + `client/components/RecordSheet/RecordSheet.uvue:709` | 注册点分散/可扩展性缺口 | P1 | 新增标签/类型有唯一登记处 | 自定义标签放开涉及产品口径与模型标签集 | 新增工具 | 新增一类标签后逐点核对是否需要改 | 批次1（后端登记处）/ 批次2（客户端） | 确认 |
| D07-16 | `backend/app/services/correction.py:74-111` + `backend/app/api/corrections.py:54-69` | 异常/事务一致性 | P1 | 消除 Qdrant 幽灵点与双提交窗口 | 引入事务边界需回归纠错全链 | 行为等价替换 | 注入 DB 提交失败，查 Qdrant 是否残留点 | 批次1 | 确认 |
| D07-17 | `backend/app/services/profile_annotator.py:274-282` + `backend/sql/schema.sql:247-254` | 数据完整性 | P2 | 证据锚点不再重复膨胀 | 加唯一约束需数据清理迁移 | 行为等价替换 | 同一 content 二次标注后查行数 | 批次3 | 确认 |
| D07-18 | `backend/app/services/interview.py:134`（`db`/`user_id` 零使用） | 死参数 | P2 | 签名与实现一致 | 无 | 纯移动+兼容再导出 | 函数体内检索 `db`/`user_id` 零命中 | 批次1 | 确认 |
| D07-19 | `backend/app/api/corrections.py:38` + `backend/app/services/correction.py:156` | 硬编码枚举重复 | P2 | source 枚举单一来源 | 无 | 纯移动+兼容再导出 | 改枚举值后核对两处 | 批次1 | 确认 |
| D07-20 | `backend/app/api/contents.py:89` + `backend/app/main.py:120` | 域边界混居 | P2 | 路由前缀与文件归属一致 | 迁移路由影响 OpenAPI 分组 | 纯移动+兼容再导出 | OpenAPI 路径不变性核对 | 批次3 | 确认 |
| D07-21 | `backend/app/services/correction.py:279,341` | 依赖方向/服务耦合 | P2 | 服务依赖显式化 | 需防真循环导入 | 纯移动+兼容再导出 | 导入图核对 + 全量测试 | 批次3 | 确认 |
| D07-22 | `client/pages/portrait/manage.uvue:310-329,420-454` | 硬编码枚举/魔法数字 | P2 | 处置档位与配色单一来源 | 配色属设计真值（画布），迁移需对照画布 | 纯移动+兼容再导出 | 画布比对 | 批次3 | 确认 |
| D07-23 | `backend/app/db/session.py:30-34` | 事务口径一致性 | P2 | 异常路径显式回滚，语义明确 | 依赖隐式回滚已工作，改动需全量回归 | 行为等价替换 | 注入异常后断言无脏写 | 批次3 | 确认 |

---

### 2.1 逐条证据

**D07-1｜部署镜像缺画像枚举 JSON（P0）**

读了 `deploy/Dockerfile.backend`（全文 49 行）与 `deploy/docker-compose.yml` L131-161。证据链：

1. `Select-String -Path "deploy\Dockerfile.backend" -Pattern "COPY"` 输出：
   ```
   35: COPY backend/requirements.txt ./requirements.txt
   39: COPY backend/ ./
   ```
   → 镜像内**只有 `backend/`**，无 `docs/`。
2. `deploy/docker-compose.yml:156-159` volumes 仅两条：`../backend/models:/app/models:ro`、`./data/hf-cache:/root/.cache/huggingface` → 无 `docs/` 挂载。
3. 全仓检索 `PROFILE_ENUM_DIR`（命令：`Get-ChildItem -Recurse -File -Include *.py,*.yml,*.yaml,*.template,*.env,*.sh,*.md | Select-String -Pattern "PROFILE_ENUM_DIR"`）**仅 2 处命中，且都在 `profile_schema.py` 自身**（L8 注释、L115 读取）→ 无任何部署配置设置该变量。
4. `profile_schema.py:21-22`：`_REPO_ROOT = Path(__file__).resolve().parents[3]`、`_DEFAULT_ENUM_DIR = _REPO_ROOT / "docs"`。

容器内 `__file__ = /app/app/services/profile_schema.py` → `parents[3] = "/"` → 默认枚举目录解析为 `/docs`，镜像内不存在；`_build_schema()` 的 `_load_json(enum_dir / L0_FILENAME)` 将抛 `FileNotFoundError`。而 `get_schema()` 被 `profile_annotator.record_hits/apply_annotation`、`llm_ops.annotate.annotate`、`interview._activate_l1_interests` 直接调用 → **容器内画像标注（入库钩子）/ 访谈 L1 激活链路首调即 500**；`pipeline_ext/profile.py:30-33` 会吞掉异常（内容入库不阻断，但画像静默不写），`api/interview.py:42` 无兜底 → 访谈端点 500。

> 诚实边界：本条目为**静态路径推导**（Dockerfile COPY 范围 + `parents[3]` 均可直接读出），**未实际构建镜像运行时复现**。标 `确认` 依据"直接代码证据"，但落地前建议补一次容器内实跑（见验证手段）。

**D07-2｜访谈关键词表与 JSON `aliases` 双实现且不一致（P1）**

读了 `backend/app/services/interview.py:49-84`，见四张硬编码表：`_RELATION_ROLE_KEYWORDS`（L49-56，20 键）、`_RELATION_FINE_KEYWORDS`（L58-65，26 键）、`_LIFE_EVENT_KEYWORDS`（L67-77，28 键）、`_VALUES_KEYWORDS`（L79-84，12 键）。

对照 `profile_schema.py:46`（`aliases: dict[str, str]`）与 `llm_ops/annotate.py:120-122`：
```python
for alias, canonical in spec.aliases.items():
    if len(alias) >= 2 and alias in text:
        matched[canonical] = max(matched.get(canonical, 0), len(alias))
```
→ **JSON `aliases` 已是权威同义归一表，且 mock 通道走它**；`interview.py` 却另建一套纯 Python 关键词表，且**只对 `important_person/life_turn/proud_thing` 三问生效**。

不一致实证（同一句"我升职了"）：走内容入库 → `annotate` → JSON `life_event_major.aliases` 含"升职" → 命中；走访谈 `life_turn` → `_LIFE_EVENT_KEYWORDS` **无"升职"键**（只有"转行/跳槽"映射到"升职跳槽转行"）→ 未命中 → 落 `profile_dimension_pending`。反向亦然：`_VALUES_KEYWORDS` 的"坚持/跑完/带大"等键在 JSON 中是否存在需逐条比对。

**结论**：同语义双实现，且**同句经不同入口得到不一致画像**，同时 `interview.py` 的表是纯增量维护点（JSON 改一处不生效）。

**D07-3｜`_classify_content` 不检查 `degraded`（P1，D-22 同族未修点①）**

读了 `backend/app/services/pipeline.py:124-132`：
```python
def _classify_content(db: Session, content: Content, text: str) -> None:
    """SetFit 5 类分类 → 回写 content_class/class_source/model_version"""
    try:
        result = _get_classifier()(text)
        content.content_class = result["label"]
        content.class_source = "setfit"
        content.model_version = "setfit-v1"
    except Exception as exc:  # noqa: BLE001 —— 分类失败静默（用户无感知）
        logger.warning("分类失败 content=%s: %s", content.id, exc)
```
对照 `classifier.py:64-76`（`_fallback_result` 返回 `{"label":"mixed","confidence":0.0,"degraded":True,...}`）与 `classifier.py:88`（`if model is None: return _fallback_result(...)`）。

→ **模型不可用时，`result["label"]` 就是 `"mixed"`，且 `result["degraded"] is True`，但本函数既不读 `degraded`，也照样把 `class_source` 写成 `"setfit"`、`model_version` 写成 `"setfit-v1"`** —— 即把"模型没跑"伪装成"SetFit 判定为混合"，且伪造来源字段。这正是 D-22 的**入库主链路根因**：`classify()` 修了 `degraded` 标记，但**这条更早、更广的写入路径没有消费它**。

检索证据：`Select-String -Pattern "class_source"` 全后端仅 3 处命中（`models/content.py:40` 定义、`pipeline.py:9` 注释、`pipeline.py:129` 赋值）→ 无任何地方按 `degraded` 分流。

**D07-4｜纠错回写 `content_class` 但不更新 `class_source` / 不重索引（P1，D-22 同族未修点④）**

读了 `backend/app/api/corrections.py:64-69`：
```python
if req.source == "active" and content.content_class != req.new_label:
    content.content_class = req.new_label
    db.commit()
```
问题一：`class_source` 保持 `"setfit"`（`pipeline.py:129` 写入）→ 用户主动纠正后的权威分类仍标称为模型产出，**溯源字段与事实不符**（与 D07-3 同族）。

问题二：`content_class` 同时是 Qdrant payload 字段——`pipeline.py:106-121` 经 `extend_payload` 写入 `"content_class": content.content_class`（`pipeline.py:110`、`pipeline_ext/payload.py:75`），`vector_store.py:383-387` 也按 `content_class` 建过滤字段。`api/corrections.py` 全文（78 行）**无任何 `upsert_content` / 重索引调用** → 回写只改 DB 列，**检索侧 payload 永久停旧值**。

检索证据：`Select-String -Pattern "content_class"` 显示消费方为 `services/rag/__init__.py:125`（类目路由过滤）、`rag/pg_fallback.py:56-57`、`events/aggregate.py:59`、`events/aggregate_write.py:347`、`api/contents.py:521`（出参）。→ 用户纠错后，**描述性搜索的类目过滤仍按旧类目走**，与 D-22 注释宣称的"内容分类展示永久停旧值"修复只完成了一半。

**D07-5｜`ArbitrateRequest.content_id` / `preferred_label` 静默丢弃（P1，D-22 同族未修点③）**

读了 `backend/app/schemas/correction.py:29-36`：
```python
# D-22（08-29 契约增量，18 号表；R2 客户端重构时消费，本波仅透传入 job）
content_id: str | None = Field(None, max_length=64, description="被裁决内容 ID（可选：层①按内容点核对最新用户意图）")
preferred_label: str | None = Field(None, description="用户所选标签（可选：层②degraded 时以用户意图为准的兜底依据）")
```
注释声明"**本波仅透传入 job**"。但读了 `backend/app/api/classify.py:100-109`：
```python
job = enqueue_idempotent("arbitrate", str(user.id), req.client_request_id,
                         arbitrate_job, str(user.id), req.text, req.content_type, meta=meta)
...
job = enqueue_high(arbitrate_job, str(user.id), req.text, req.content_type, meta=meta)
```
与 `backend/app/services/correction.py:292`：
```python
def arbitrate_job(user_id: str, text: str, content_type: str = "text") -> dict:
```
→ **两个字段都没有被透传**，`arbitrate_job` 签名也不接收，pydantic 解析后即被丢弃。注释宣称的"仅透传入 job"**未实现**，属契约声明漂移（文档说有、代码没有）。

**D07-6｜客户端不解析 `degraded`（P1，D-22 同族未修点②）**

读了 `client/utils/text_recorder.uts:171-186`：
```ts
function parseClassifyResult(r: UTSJSONObject): ClassifyResult {
	...
	return new ClassifyResult(
		r.getString('label') ?? '',
		r.getString('label_cn') ?? '',
		r.getNumber('confidence') as number,
		scores
	)
}
```
→ 只解析 label/label_cn/confidence/scores，**不解析 `degraded`**；`ClassifyResult` 类定义亦无该字段。后端 `schemas/classify.py:24-25` 已把 `degraded: bool = False` 纳入契约、`api/classify.py:80` 用 `ClassifyResult(**result)` 直出 → 客户端 DTO 与契约不对称。

> 影响面窄的诚实注记：`client/utils/text_recorder.uts:205-216` 记录 `submitArbitrate/pollArbitrate` 已于 `f022938` 删除且"不应再接回"；现存 `pollClassify` 调用点（`RecordSheet.uvue:986-989`）只 `console.log` 结果，**当前无 UI 消费方**。故本条是结构债（契约字段丢失），不是现网可见缺陷。

**D07-7｜英文维度 id 当标签名 + 伪造置信度（P1）**

读了 `client/pages/portrait/manage.uvue:222-255`，关键段 L236-249：
```ts
for (let i = 0; i < Math.min(6, keys.length); i++) {
	const key = keys[i]
	const values = dims.getJSONArray(key)
	let conf = 70
	if (values != null && values.length > 0) {
		conf = 60 + (i * 5) % 30
	}
	tags.push({ name: key, confidence: conf, active: i < 3 })
	names.push(key)
}
```
问题一：`name: key` —— `key` 来自 `interview/profile` 的 `dimensions` 字典键，即**英文维度 id**（`relation_role` / `life_event_major` / `values_priority` / `relation_core`…，见 `profile_annotator.py:335-351` `display_dimensions` 返回 `{dim: [值]}`）。用户看到的是 `relation_role` 这类内部标识，而非 `profile_schema.py:38` 的 `label`。
问题二：`confidence: 60 + (i * 5) % 30` 是**纯按序号编造的伪置信度**，与后端 `user_profile.dimensions` 中真实 `confidence`（`profile_annotator.py:201/218/236` 写入）无关 —— 与 D-16（情绪"平静"伪造）同族：**把编造值当模型结论展示**。

同族第二处：`manage.uvue:420-454` `rebuildTagsFromStorage` 硬编码 `confidence: 70`（L432/439）与 `confidence: 100`（L447）。

**D07-8｜`fallbackQuestions` 文案与后端语义错配（P1）**

读了 `client/pages/interview/interview.uvue:84-89`：
```ts
// 兜底三问（API 未就绪时使用，与设计稿冷启动访谈一致）
const fallbackQuestions: Array<InterviewQuestion> = [
	new InterviewQuestion('important_person', '最近有什么小事，让你印象很深？'),
	new InterviewQuestion('life_turn', '今天有什么瞬间，值得被记住？'),
	new InterviewQuestion('proud_thing', '此刻，最想对谁说一句话？')
]
```
后端 `services/interview.py:39-43` 的三问是"你生命中最重要的人是谁？/ 你经历过的最重要的人生转折是什么？/ 你最骄傲的一件事是什么？"。

→ **key 保留、文案换成完全无关的问题**。`submitAll`（L271-285）把 `answers` 原样 POST；后端 `_extract_hits`（L109-131）按 key 语义做关键词匹配 → 用户对"今天有什么瞬间值得记住"的回答会被拿去匹配 `life_event_major` 关键词表，命中即写入画像 → **画像污染**。而 `onLoad`（L123-129）在 `fetchInterviewQuestions()` 返回空数组时才保留兜底，即**后端不可达时正是最需要正确兜底的时刻**。

**D07-9｜`confirmation` 复述确认闭环未接线（P1）**

读了 `client/pages/interview/interview.uvue`：状态声明 L117 `let confirmation = ref('')`；`submitAll` L280-282 赋值：
```ts
if (res != null && res.confirmation != '') {
	confirmation.value = res.confirmation
}
```
模板 L28-34 完成卡：
```html
<view v-else class="n4_181"> <!-- 完成卡（三问答毕） -->
	<text class="n4_182">访谈完成</text>
	<text class="n4_183">谢谢你的分享，已经开始了解你了</text>
	<view class="start-btn" @tap="goHome">...
```
→ **`confirmation` 全模板零渲染**（`Select-String` 检索 `interview.uvue` 中 `confirmation` 仅命中 L117 声明与 L281 赋值，无模板插值）→ 死状态。后端 `services/interview.py:175-184` `_build_confirmation` 已产出完整复述文本并经 `api/interview.py:43` 出参 → **B1-7 复述确认闭环"后端已通、前端未接"**；`services/interview.py:14` 注释声称的"档案确认闭环：回答 → 复述文本 → 对话式修改（B1-6 后续）"整体未落地。

**D07-10｜"清除画像数据"为本地安慰剂（P1）**

读了 `client/components/PortraitPrivacyPanel/PortraitPrivacyPanel.uvue:82-100`：
```ts
uni.showModal({
	title: '清除画像数据',
	content: '将删除全部画像维度与证据锚点，此操作不可恢复。确定清除吗？',
	success: (res) => {
		if (res.confirm) {
			uni.setStorageSync('privacy_ai_chat', '')
			uni.setStorageSync('privacy_echo', '')
			uni.setStorageSync('portrait_locked_tags', '')
			uni.setStorageSync('portrait_ai_tags_edited', '')
			...
			uni.showToast({ title: '画像数据已清除（本地）', icon: 'none' })
```
→ 强确认文案承诺"删除全部画像维度与证据锚点、不可恢复"，实际**只清 4 个本地 storage key**，服务端 `user_profile.dimensions` / `profile_l2_evidence` / `profile_dimension_history` **完全不动**。代码注释已诚实标注"后端画像清除端点未登记（台账 卡25 F9c）"，toast 也标了"（本地）"，**但 modal 正文的承诺文本未降级** → 用户读到的是"已删除全部画像维度"。

宿主侧 `manage.uvue:456-461` `onPortraitCleared` 同样只清本地态，并注明"不重拉后端防复活"——**即页面刷新后画像会"复活"**。

**D07-11｜`ProfileSensitive.locked` 只写不读，且可被静默解除（P1）**

读了 `backend/app/services/echo.py:59-70`：
```python
def _profile_hit(text: str, rows) -> bool:
	for row in rows:
		topic = (row.topic or "").strip()
		if not topic:
			continue
		if topic in text and row.disposition in PROFILE_BLOCK_DISPOSITIONS:
			return True
	return False
```
→ **只读 `disposition`，从不读 `locked`**。全后端检索 `\blocked\b`（命令：`Get-ChildItem -Recurse -File -Include *.py -Path backend | Select-String -Pattern "\blocked\b"`）命中 12 处，逐条核对：`api/contents.py:460/477`（出参/入参）、`models/profile.py:83`（列定义）、`schemas/content.py:80/90`（契约）、`echo.py:79/87/101/108`（upsert 形参/写入）、`export.py:137`（导出）、`pipeline_ext/sensitive.py:13/139`（注释）、迁移 2 处（列定义）、`tests/test_echo.py:243/244/307`（断言写读）—— **无一处以 `locked` 改变行为分支**。

而 `echo.py:97-112` 的 `on_conflict_do_update` 把 `locked` 一并 `set_`：
```python
set_={"disposition": disposition, "evidence": evidence_list, "locked": locked, "updated_at": func.now()}
```
→ 后续任意请求（含 `ProfileSensitiveCreate.locked` 默认 `False`，`schemas/content.py:80`）**可把用户显式锁定降级为 False**。这直接违反"用户操作优先"铁律，且使 `echo.py:48/87` 宣称的"locked=True 为用户显式标记（永不过期语义强化）"成为**无实现支撑的声明**。

**D07-12｜画像主数据无人工确认/修改/锁定端点（P1）**

检索全后端路由：`backend/app/api/` 下与画像相关的端点仅 3 个（`api/interview.py:26/36/46` 只读+提交答案）与 `api/contents.py:466/484/501`（画像级敏感 CRUD）。**无任何"查看维度详情 / 修改维度值 / 删除某维度 / 锁定维度"端点**。

对照 AGENTS.md 关键约束"用户操作优先：手动合并/拆分/确认后，自动算法永不覆盖"：本域中 `profile_annotator.apply_annotation`（L114-191）对任意维度执行 `_apply_single`（异值即 `replaced`，L208-211 把旧值压入 history）或 `_apply_multi`（异值即追加），**`source="annotation"` 的自动标注可覆盖此前任何写入**，且用户无任何途径声明"这条别再改"。

`services/interview.py:14` 注释自认"档案确认闭环：回答 → 复述文本 → 对话式修改（B1-6 后续）"——**该后续未实现**。

**D07-13｜`profile_schema.validate()` 硬编码维度数（P1）**

读了 `backend/app/services/profile_schema.py:107-110`：
```python
if self.l0_count != 51:
	issues.append(f"L0 维度数异常: {self.l0_count}（期望 51）")
if self.l1_count != 193:
	issues.append(f"L1 维度数异常: {self.l1_count}（期望 193）")
```
→ 新增/删除任一维度必须改这 2 行代码，与文件头 L6 自述"**全部数据驱动、代码零硬编码**"直接矛盾。

**D07-14｜三套 `dimensions` 读路径（P1）**

1. 后端展示态：`profile_annotator.py:335-351` `display_dimensions()` → `{dim: [值]}`（`interview.py:202/212` 使用）；
2. 客户端摘要：`client/utils/play.uts:155-172` `flattenDimensions()` → `"relation_role：家人、伴侣；..."`（同样暴露英文 dim id）；
3. 导出原始态：`export.py:_profile_out()` → `profile.dimensions` 原样结构化 JSON。

→ 同一份数据三套读法，且**两套（1、2）都把内部维度 id 直接交付用户**（与 D07-7 同源）。任一处口径变更需同步 3 处。

**D07-15｜新增纠错类型/标签的注册点分散（P1）**

| 注册点 | 位置 |
|---|---|
| ① 标签集与中文词表 | `backend/app/services/classifier.py:27-32`（`DEFAULT_CLASSES` / `DEFAULT_CLASSES_CN` / `VALID_CLASSES` / `LABEL_CN_MAP`） |
| ② 模型侧标签顺序真值 | `backend/models/setfit-classifier/labels.json`（`classifier.py:54-60` 读取） |
| ③ API 入参白名单校验 | `backend/app/api/corrections.py:36`（`req.new_label not in VALID_CLASSES` → ERR_CORR_001） |
| ④ 契约文档字符串 | `backend/app/schemas/correction.py:8`（`"todo/idea/emotion/quote/mixed"` 字面量，与 ① 无编译期绑定） |
| ⑤ 客户端标签枚举 | `client/utils/text_recorder.uts`（`ALL_LABELS`，被 `RecordSheet.uvue:259` 引入） |
| ⑥ 客户端自定义标签 | `client/components/RecordSheet/RecordSheet.uvue:707-709` —— 注释明示"后端 corrections.py VALID_CLASSES 白名单不支持自由标签 → 自定义选中时保存**跳过** submitCorrection（台账 A 类登记）" |

→ 新增一类标签需改 **①③④⑤**（+②模型）；且 ⑥ 表明**用户自定义标签当前被静默丢弃**（不报错、不落纠错），是可扩展性缺口的现网表现。

**D07-16｜纠错写入 Qdrant/DB 非原子 + 两次提交（P1）**

读了 `backend/app/services/correction.py:74-111`：先 `store.upsert(...)`（L78-96，Qdrant）→ 再 `db.add(row); db.commit()`（L109-111，PG）。→ **Qdrant 成功而 PG 失败时留下幽灵向量点**（后续 `apply_personal_rule` 会命中它并返回一个 DB 中不存在的 `correction_id`）。另 `_trim_user_corrections`（L118-150）在裁剪时又各做一次 `store.delete` + `db.commit()`。

叠加 `backend/app/api/corrections.py:54-69`：`record_correction` 内部已 `commit()`，回写 `content_class` 后再 `commit()` → **单次 HTTP 请求内两次提交**，两次提交之间进程崩溃则"纠错记录已落库、权威分类未回写"，状态不一致。

**D07-17｜`_write_l2_evidence` 无去重（P2）**

读了 `backend/app/services/profile_annotator.py:274-282`：裸 SQL
```python
"INSERT INTO profile_l2_evidence (dimension, user_id, evidence_content_ids) VALUES (:d, :u, CAST(:c AS jsonb))"
```
无 `ON CONFLICT`。核对建表：`backend/migrations/versions/431bcaa8bd54_baseline_from_orm.py:665-672` 只有 `PrimaryKeyConstraint('id')` 与外键；`a7b8c9d0e1f2_add_p1b_perf_indexes.py:29` 建的是普通索引 `idx_l2_evidence_user_dim`；`backend/sql/schema.sql:247-254` 同口径 → **无 (dimension, user_id) 唯一约束**。`apply_annotation` L181-182 每命中一次即插一行 → 同一内容重复标注（重入库/重试）会产生重复证据锚点。

**D07-18｜`_activate_l1_interests` 死参数（P2）**

读了 `backend/app/services/interview.py:134-152`：签名 `(db: Session, user_id: str, answers: dict[str, str])`，函数体内仅使用 `answers`（L141）、`get_schema()`（L144）、`annotate()`（L146），**`db` 与 `user_id` 零使用**（检索函数体 134-152 行区间内 `db`/`user_id` 无命中）。调用方 `submit_answers` L193 仍传入两个实参。

**D07-19｜source 枚举两处重复定义（P2）**

`backend/app/api/corrections.py:38`：`if req.source not in ("active", "echo", "org")`
`backend/app/services/correction.py:156`：`PASSIVE_SOURCES = ("echo", "org")`
`backend/app/schemas/correction.py:10` 描述字符串再次枚举 `"active=主动纠错 / echo=回响确认 / org=整理联动"`
→ 同一枚举散落 3 处（其中 API 层是全量三元组，服务层是被动二元组子集），新增 source 需同步多处。

**D07-20｜`profile_sensitive_router` 物理域混居（P2）**

`backend/app/api/contents.py:89`：`profile_sensitive_router = make_router(prefix="/api/v1/profile", tags=["profile-sensitive"])` —— 定义在 contents 模块内，前缀却是 `/api/v1/profile`；注册在 `backend/app/main.py:120`。三端点实现 L466/484/501，均委托 `services.echo`（L473/491/508）。→ 文件归属与路由前缀不一致，域边界不清晰。

**D07-21｜`services/correction.py` 函数级导入 classifier（P2）**

`backend/app/services/correction.py:279`：`from app.services.classifier import classify`（在 `arbitrate` 函数体内）
`backend/app/services/correction.py:341`：`from app.services.classifier import LABEL_CN_MAP`（在 `_label_cn` 函数体内）
→ 服务层服务间耦合被藏在函数体内（虽注释称防循环依赖，但 `classifier.py` 并不 import `correction`，见 `classifier.py` 全文 import 段 L8-25 → **无真实循环**，该规避属多余）。

**D07-22｜客户端硬编码处置档位与配色（P2）**

`client/pages/portrait/manage.uvue:310-319` `dispositionLabel()` switch 硬编码 5 档（`allow/mention/caution/review/forbid`），与后端 `services/echo.py:31` `PROFILE_DISPOSITIONS` 重复；L323-329 `badgeStyle()` 硬编码 5 组配色 hex。L432/439/447 硬编码 `confidence: 70/100`。

**D07-23｜`get_db` 不显式 rollback（P2）**

`backend/app/db/session.py:30-34`：
```python
def get_db() -> Generator[Session, None, None]:
	db = SessionLocal()
	try:
		yield db
	finally:
		db.close()
```
→ 未捕获异常时不显式 `db.rollback()`，依赖 `close()` 的隐式回滚。与 `profile_annotator.py:320-323` 注释所描述的"避免 rollback 误伤整笔"设计意图一致（属有意取舍），但口径未在 `session.py` 侧显式声明，易被后续修改者误判。

---

## 3. 纠错恒 mixed（D-22 / O-2）修复核实专章

### 3.1 任务给定哈希不可解析 —— 文档漂移（首要发现）

任务描述与 AGENTS.md 均称"D-22 后端半已于 commit `81424fb` 修复"。实测：

```
$ git cat-file -t 81424fb
fatal: Not a valid object name 81424fb          # exit=128
```
（`git show 81424fb` 同样 exit 128；`git log --all --oneline -S "81424fb"` 无结果）

改用代码内容反查实际入库提交：
```
$ git log --oneline -S "D-22（08-29，P1 回写）" -- backend/app/api/corrections.py
b2d1bfa R9-B3/B4批次入库+事故后快照重建: ...

$ git log --oneline -S "degraded" -- backend/app/services/classifier.py
b2d1bfa R9-B3/B4批次入库+事故后快照重建: ...

$ git merge-base --is-ancestor b2d1bfa HEAD ; echo $?
0                                                # 已在 HEAD 祖先链上
```
→ **D-22 修复代码确在库中且已进入 `develop` 主线，但权威文档引用的哈希 `81424fb` 在本仓库不可解析**（疑为 rebase 前哈希，或 `.git` 回收站恢复后索引丢失所致——`b2d1bfa` 的提交信息自述"本 commit 以工作树全量快照重建断点"）。**这是审计可复现性缺口：任何按文档哈希复查 D-22 的人都会失败。**

### 3.2 修复代码位置与当前状态（逐点核实）

| # | 修复点 | 位置 | 当前代码 | 状态 |
|---|---|---|---|---|
| 1 | 降级结果显式标记 | `backend/app/services/classifier.py:64-76` | `_fallback_result()` 返回 `{"label":"mixed","label_cn":"混合","confidence":0.0,"degraded":True,...}` | 已生效 |
| 2 | 正常路径标记 `degraded:False` | `classifier.py:95`（`classify`）、`classifier.py:122`（`classify_batch`） | 均含 `"degraded": False` | 已生效 |
| 3 | 裁决链透传 | `backend/app/services/correction.py:281-289` | `"degraded": bool(result.get("degraded", False))`（层②）；层①返回 `"degraded": False`（L277） | 已生效 |
| 4 | 契约增量 | `backend/app/schemas/classify.py:24-25` | `ClassifyResult` 含 `degraded: bool = False`；`api/classify.py:80` 以 `ClassifyResult(**result)` 直出 | 已生效 |
| 5 | 主动纠错回写权威字段 | `backend/app/api/corrections.py:64-69` | `if req.source == "active" and content.content_class != req.new_label: content.content_class = req.new_label; db.commit()` | 已生效（有缺口，见 3.4） |
| 6 | 测试守护 | `backend/tests/test_correction.py:239-254` `test_arbitrate_degraded_passthrough`（monkeypatch `_load` 返回 None → 断言 `degraded is True`）；`:257-286` `test_correction_active_writes_back_content_class`（active 回写 / echo 不回写）；`:289+` `test_correction_soft_deleted_content_rejected` | 均存在 | 已生效 |

**结论一行**：**D-22 后端半修复代码已生效且有测试守护，但文档引用的提交哈希 `81424fb` 在本仓库不可解析（实际入库提交为 `b2d1bfa`，已确认是 HEAD 祖先）。**

### 3.3 客户端半核实

- 正向：`client/utils/text_recorder.uts:205-216` 明确记录 `submitArbitrate/pollArbitrate/pollArbitrateTick` 已于 `f022938` 删除且"不应再接回"，理由链引用 `docs/P2诊断_O1O2_20260829.md §2.1 RC1`（旧 `record.uvue onTagTap` 先仲裁再用回声覆盖用户所选 → 层①学不到用户意图 → 自我强化 → 恒混合）。
- 正向：`client/components/RecordSheet/RecordSheet.uvue:976-981` 与 `:1328` 现状为 `submitCorrection(cid, text, picked, '')` —— **用户所选先落、无仲裁覆盖**，符合"用户操作优先"。
- 缺口：`client/utils/text_recorder.uts:171-186` 的 `ClassifyResult` 不含 `degraded`（见 D07-6）。

### 3.4 同族未修点清单（4 条）

| # | 位置 | 性质 |
|---|---|---|
| ① | `backend/app/services/pipeline.py:124-132` | 入库主链路不检查 `degraded`，模型不可用时把 `mixed` 写成权威 `content_class` 并标 `class_source="setfit"`（**伪造来源**）——「纠错恒 mixed」的入库根因仍在 |
| ② | `client/utils/text_recorder.uts:171-186` | 客户端 DTO/解析器不消费 `degraded`（当前无消费方，影响面窄） |
| ③ | `backend/app/schemas/correction.py:29-36` + `api/classify.py:100-109` + `services/correction.py:292` | `ArbitrateRequest.content_id` / `preferred_label` 契约增量被静默丢弃，注释宣称的"透传入 job"未实现 |
| ④ | `backend/app/api/corrections.py:64-69` | 回写 `content_class` 但不更新 `class_source`（仍 `"setfit"`）、不重索引 Qdrant payload → 溯源字段与检索侧类目过滤停旧值 |

---

## 4. 画像不可逆性保护专章

项目关键约束：**"软删除全局 30 天；画像丢 = 数月积累不可逆（备份 RPO≤24h / WAL≤5min）"**。

### 4.1 写入事务保护

| 路径 | 提交边界 | 评价 |
|---|---|---|
| `profile_annotator.record_hits`（L63-111） | 批内所有命中（池/维度/证据/历史）末尾**单次** `db.commit()`（L110） | ✅ 批内原子 |
| `interview.submit_answers`（L187-204） | `_queue_unmapped`（扩展队列）+ `record_hits`（画像）**同一 session、同一 commit** | ✅ 注释 L195 明示"与画像写入同一事务" |
| `echo.upsert_profile_sensitive`（L97-114） | `pg_insert ... on_conflict_do_update` **单语句原子 upsert** + `db.commit()` | ✅ 无竞态窗口（R2#13 已修） |
| `profile_annotator.get_or_create_profile`（L315-332） | `on_conflict_do_nothing` 竞态兜底，**不用** `IntegrityError+rollback`（注释 L322-323 说明：避免误伤整笔待提交写） | ✅ 设计正确 |
| `api/corrections.py`（L54-69） | **两次** `db.commit()`（`record_correction` 内 L110 + 回写后 L69） | ⚠️ 见 D07-16 |
| `db/session.py:get_db`（L30-34） | 仅 `close()`，无显式 `rollback()` | ⚠️ 见 D07-23（依赖隐式回滚） |

### 4.2 审计与历史留痕

- `profile_dimension_history`（`models/profile.py:26-37`）：每次 `apply_annotation` 写一行（`profile_annotator.py:185`），写入侧裁剪至最近 `HISTORY_LIMIT=10` 条（L33、L285-311）。✅ **DB 级历史存在**。
- `user_profile.dimensions` 内嵌 `history`（仅**单值**维度，`_apply_single` L206-211，上限 10）。⚠️ **集合型维度（`_apply_multi` L226-253）无内嵌 history** —— 异值追加、**从不移除**，故集合型维度不存在"旧值丢失"，但也因此**无法回退**。
- `profile_l2_evidence`（内容级溯源）：`profile_annotator.py:274-282` 写入；表无唯一约束（见 D07-17）；`c7d8e9f0a1b2_add_l2_evidence_cascade.py` 已补 `ON DELETE CASCADE`（用户删除时随删）✅。
- `profile_annotation_pool`（低置信度候选，`_add_to_pool` L257-270）：`status="pending"` 待周级复核，**只进不出**（检索无消费/回写路径）⚠️。

### 4.3 备份链路（对照约束逐半核实）

读了 `deploy/scripts/backup_pg.sh`（全文 104 行）与 `deploy/docker-compose.yml`：

- **RPO ≤24h 半边：✅ 达成**。L41-75：每日 cron（L14 示例 `30 3 * * *`）`pg_dump -U $PG_USER -d $PG_DB -Fc` → 先写 `.part` → 非空校验（`MIN_BYTES=10240`，L29/61-65）→ `pg_restore -l` 归档结构校验（L67-73）→ 校验通过才 `mv` 改名（L74）→ 按天轮转保留 7 天（L91）。`deploy/RUNBOOK.md` 定时任务表亦登记 03:30 PG 备份。
- **WAL ≤5min 半边：❌ 未达成（刻意取舍）**。`deploy/docker-compose.yml:56`：`- archive_mode=${PG_ARCHIVE_MODE:-off}`；L73 注释"WAL 归档（PITR，RPO ≤5min）：S3 卡已接线，**默认 off**"；`backup_pg.sh:16-19` 给出理由："archive_command 若因目录属主/磁盘满而失败，PG 会持续保留 WAL 直至 pg_wal 打满磁盘——'未验证就默认开启'比'默认关闭+明确开启步骤'更危险"。脚本 §4（L77-88）在开启时会检查 `pg_stat_archiver` 失败计数并告警。

> **口径结论**：`docs`/AGENTS 中的"备份 RPO≤24h/WAL≤5min"是**约束上限口径**，实际落地为"dump 半边达成、WAL 半边默认关闭（有显式开启开关与告警）"。**这不是缺陷而是有文档支撑的刻意取舍**，但**画像数据的 PITR 能力当前为 0**，故登记于此以便台账口径对齐（非条目表条目）。

### 4.4 「用户操作优先」铁律在本域的落实度

| 子项 | 现状 | 判定 |
|---|---|---|
| 纠错链路（用户手选标签） | `RecordSheet.uvue:976-981/1328` 先 `submitCorrection(用户所选)`，仲裁客户端路径已删；`api/corrections.py:67` 仅 `active` 回写权威字段 | ✅ 落实 |
| 画像维度（用户确认/修改/锁定） | **无端点、无字段、无 UI**（D07-12） | ❌ 未落实 |
| 画像级敏感（用户"别跟我提 X"） | 有 CRUD（`api/contents.py:466/484/501`）+ UI 强确认；但 `locked` 只写不读且可被静默解除（D07-11） | ⚠️ 半落实 |
| 清除画像（用户显式撤回） | 仅清本地 4 个 storage key，服务端不动（D07-10） | ❌ 未落实 |
| 自动标注可否覆盖用户结论 | `apply_annotation` 对任意维度可 `replaced`/追加，无用户锁位（D07-12） | ❌ 未落实 |

---

## 5. 依赖方向与抽象覆盖度结论

### 5.1 依赖方向

```
api/interview.py ──► services/interview.py ──► services/profile_annotator.py ──► services/profile_schema.py
                                          └──► services/llm_ops/annotate.py ──► services/profile_schema.py
api/corrections.py ─► services/correction.py ─(函数级)► services/classifier.py
                                              └──► services/embedding.py / vector_store.py
api/classify.py ────► services/correction.py (arbitrate_job) / services/classifier.py (classify_job)
api/contents.py ────► services/echo.py ──► db/models/profile.py
services/pipeline.py ─► services/pipeline_ext/profile.py ─► services/profile_annotator.py
                     └► services/pipeline_ext/sensitive.py ─► services/echo.py (profile_sensitive 双查)
services/export.py ──► db/models (UserProfile / ProfileSensitive / CorrectionLog)
```

**边界清晰度评价**：

- ✅ `profile_schema`（纯数据加载，零下游依赖）、`profile_annotator`（唯一画像写入口，`record_hits` 被 `interview` 与 `pipeline_ext/profile` 共同复用）—— 分层正确。
- ✅ `interview` → `profile_annotator`（经 `record_hits`）方向正确，无反向依赖。
- ⚠️ **`api/contents.py` 承载 `/api/v1/profile` 前缀路由**（D07-20）：API 层域边界混居。
- ⚠️ **`services/correction.py` 函数级导入 `services/classifier.py`**（D07-21）：服务层横向耦合，且规避的"循环依赖"实际不存在。
- ⚠️ `services/echo.py` 一文件承担两类职责：回响主流程（`get_today_echo`）+ 画像级敏感 CRUD + 双查（文件头 L10-11 自述）→ 职责混居，`profile_sensitive` 的 3 个 API 端点全部委托它。
- ⚠️ `profile_schema` 的**运行时外部依赖**：枚举真值在仓库 `docs/`（不在 `backend/` 包内）→ 构成**包边界与部署边界的不一致**（D07-1）。

### 5.2 注册表 / 端口抽象覆盖度

**新增一个画像维度（L0 或 L1）需改的注册点（全部列出）**：

| # | 位置 | 必须改？ |
|---|---|---|
| 1 | `docs/画像维度枚举集_l0.json` 或 `docs/画像维度枚举集_l1_骨架.json`（`profile_schema.py:23-24` 定义文件名） | ✅ 真值源 |
| 2 | `backend/app/services/profile_schema.py:107-110`（硬编码 `l0_count != 51` / `l1_count != 193`） | ✅ **代码改动**（D07-13） |
| 3 | `backend/app/services/interview.py:49-84` 四张关键词表（若该维度属三问覆盖范围） | ✅ 否则访谈路径不生效（D07-2） |
| 4 | `backend/app/services/interview.py:89-93` `_DIM_BY_QUESTION`（三问 → 维度映射） | ✅ 若需由访谈激活 |
| 5 | `client/pages/interview/interview.uvue:85-89` `fallbackQuestions` | ✅ 若属三问 |
| 6 | `client/pages/portrait/manage.uvue:236`（`Math.min(6, keys.length)` 前 6 维截断 + 英文 id 直出） | ✅ 展示侧（D07-7） |
| 7 | `docs/画像维度枚举集_l0.json` 的 `annotate_default_dims`（`profile_schema.py:157` 读取 → `EnumSchema.annotate_dims` L77-81） | ✅ 若需进默认标注池 |
| 8 | `backend/app/services/profile_schema.py:26-27` `_DISCLOSURE_LEVELS` | ⬜ 仅当新增披露层级（非维度） |

→ **共 6-7 处，其中 2 处是纯代码（#2 硬编码计数、#3 关键词表）**，与"代码零硬编码"目标差距明确。

**新增一类纠错标签/类型需改的注册点**：见 D07-15 表（6 处，其中客户端自定义标签路径**当前被静默跳过**）。

**新增一种画像级敏感处置档位需改**：`backend/app/services/echo.py:31` `PROFILE_DISPOSITIONS` + `:30` `PROFILE_BLOCK_DISPOSITIONS` + `backend/app/schemas/content.py:74` 描述字符串 + `client/pages/portrait/manage.uvue:310-319/323-329`（switch + 配色）+ 画布真值（`docs` 侧）。→ 5 处。

### 5.3 可扩展性缺口汇总

- **硬编码枚举**：`profile_schema.py:107-110`（维度数 51/193）、`interview.py:49-84`（4 张关键词表）、`corrections.py:38` + `correction.py:156`（source 三元组）、`echo.py:30-31` + `manage.uvue:310-319`（处置 5 档）、`classifier.py:27-32`（5 类标签）。
- **魔法字符串**：`manage.uvue:241` `60 + (i * 5) % 30`（伪置信度公式）；`manage.uvue:432/439/447` `confidence: 70/100`；`manage.uvue:290-291` 主题配色/图标数组（本域外，但同页）。
- **if/elif 链**：`manage.uvue:310-319`（switch 5 档）、`manage.uvue:323-329`（5 条 `if` 配色）。后端未见长 if/elif 链（画像逻辑以 schema 驱动，设计良好）。
- **假象式抽象**：`profile_schema.py` 自述"零硬编码"但 `validate()` 硬编码计数 → **抽象承诺与实现不符**，是本域最典型的结构债。

---

## 6. 本域已查无问题项

以下项目**经实读/检索核实，未发现问题**，登记以说明覆盖度（避免后续重复审计）：

1. **`client/utils/event_ops.uts` 与本域零耦合** ✅
   检索命令：`Select-String -Path "client\utils\event_ops.uts" -Pattern "profile|correction|interview|sensitive"` → **0 命中**。该文件（466 行）为事件手动操作（confirm/merge/split）+ 离线队列，属事件域，本域无需登记问题。（任务清单虽列入，实为邻域文件。）

2. **画像标注 fail-safe 设计正确** ✅
   `pipeline_ext/profile.py:25-33` 全异常捕获仅记日志（注释 L10 明示"绝不阻断内容入库"）；`pipeline.py:138-143` 在 `_process_text` 内调用。方向正确，符合"画像失败不阻断内容入库"。

3. **`_mock_annotate` 为 schema 驱动、零硬编码规则** ✅
   `llm_ops/annotate.py:107-129`：遍历 `spec.values` + `spec.aliases` 做子串匹配，长词优先（L125 排序）、单字跳过（L118/121 `len >= 2`）、单值维度取 1 条 / 集合型取 3 条（L126）。与真实 LLM 通道同构（同字段），切换 key 无代码改动 —— **这是本域设计最好的一处，可作为 D07-2 收敛的目标形态**。

4. **访谈契约校验完备（fail-closed）** ✅
   `schemas/interview.py:6,18-36`：`ANSWER_KEYS` 白名单（未知键 422）+ 2000 字上限；用 `PydanticCustomError` 而非 `ValueError`（L26 注释说明：避免 `ctx.error` 携带不可序列化对象导致 422 信封序列化 500）。`api/interview.py:26-51` 三端点均需鉴权。

5. **`get_or_create_profile` 竞态兜底设计正确** ✅
   `profile_annotator.py:315-332`：`on_conflict_do_nothing` 原子插入，且**刻意不用** `IntegrityError + rollback`（L322-323 说明：本函数可能被已有待提交写的事务内调用，rollback 会误伤整笔）—— 与 `record_hits` 单次 commit 的批内原子语义自洽。

6. **`upsert_profile_sensitive` 的 identity-map 处理正确** ✅
   `echo.py:113-117`：Core 写后 `db.expire_all()`（因 `SessionLocal(expire_on_commit=False)`，不 expire 会返回插入时的旧值），随后重查返回最新行 —— 处理正确，无隐性 bug。

7. **画像级敏感「永不过期」无降级路径，与事件级分离清晰** ✅
   `echo.py:44-56` `profile_sensitive_blocked` 无有效期逻辑；`pipeline_ext/sensitive.py:139` 注释明确事件级走"≥3 次提及降级"（`DOWNGRADE_MENTION_THRESHOLD=3`），画像级不走本钩子。两级敏感边界清晰。

8. **纠错三道防噪音闸门实现与设计一致** ✅
   `correction.py:153-192`：被动来源（`PASSIVE_SOURCES=("echo","org")`）需同 `(user, old→new, content_type)` 一致 ≥3 次（`PASSIVE_SOURCE_MIN_CONSISTENT=3`，L159-173）+ 3 天回改窗口检测（`REVERT_WINDOW_DAYS=3`，L176-192）→ `apply_personal_rule` L230-245 逐条执行。与 `B5-c-5` 设计口径一致。

9. **纠错裁剪与共性回流链路完整** ✅
   `correction.py:118-150` `_trim_user_corrections`（保留最近 `MAX_PER_USER=500`，Qdrant 点同步删）；`correction.py:303-327` `mark_global_candidates` 单条 SQL 聚合 + 批量 UPDATE（注释 L306-307 说明原 O(全表) 内存问题已修）；触发入口 `backend/scripts/reflow_global.py:114-119` 存在（注释 L114 说明原"流水线断裂"已修）；`global_candidate_count` L330-336 供 `GLOBAL_RETRAIN_THRESHOLD=50` 判定。

10. **纠错契约的归属与软删校验正确** ✅
    `api/corrections.py:36-53`：`new_label` 白名单（ERR_CORR_001）、`source` 白名单（ERR_CORR_002）、`content` 归属 + **`deleted_at.is_(None)`** 过滤（ERR_EVENT_005/404，注释 L42-45 说明为"波D ② 补漏"）；`api/classify.py:73-75/128-130` job 归属校验（`job.meta.user_id` → 403）。安全纵深完备。

11. **`classifier` 标签词表已收敛（P1-09 已修）** ✅
    `classifier.py:30-32` 单一权威词表 `VALID_CLASSES` / `LABEL_CN_MAP`；`correction.py:339-343` `_label_cn` 引用之；`corrections.py:24` 亦引用 `VALID_CLASSES` —— **纠错侧已消除三份重复定义**（残留的 source 枚举重复见 D07-19，属另一枚举）。

12. **画像备份 dump 半边（RPO ≤24h）实现扎实** ✅
    见 §4.3：`.part` 两段式改名防半截文件、非空校验、`pg_restore -l` 归档结构校验 + 退化路径、按天轮转、季度恢复演练指引（`backup_pg.sh:96-104`）。

13. **`profile_l2_evidence` 已补 ON DELETE CASCADE** ✅
    `backend/migrations/versions/c7d8e9f0a1b2_add_l2_evidence_cascade.py`（2026-08-26 集成对齐）—— 用户删除时证据锚点随之删除，无孤儿行。

14. **客户端画像页敏感话题 CRUD 链路完整（含中文 query 处理说明）** ✅
    `manage.uvue:331-390`：`loadSensitive`（GET）/ `onRemoveSensitive`（DELETE + 强确认）/ `confirmAddSensitive`（POST 幂等 upsert，默认 `forbid` + `locked=true`）；L354-355 注释说明中文 topic 直拼 query 依赖 OkHttp 自动 percent-encode（UTF-8），并登记 iOS 适配风险 —— 属已登记的已知边界，非缺陷。

---

## 附录：审计方法与环境注记

- **只读保证**：本次审计未对任何业务文件执行写入。唯一写入 = 本报告文件。审计前后 `git status --short` 均为 `?? .codebuddy/` + `?? .trae/specs/refactor-extensibility-maintainability-wave/audit/`（非业务代码）。
- **检索工具**：本环境 `Grep` 工具不可用（`rg execution error: program not found`），全部内容检索使用 PowerShell `Select-String` / `Get-ChildItem -Recurse | Select-String` 完成；行号均取自 `Read` 工具的 `cat -n` 输出或 `Select-String` 的 `LineNumber`，**无编造行号**。
- **未覆盖部分（诚实边界）**：
  1. `backend/tests/test_profile_annotator.py`（267 行）、`test_echo.py`（295 行）、`test_classify.py` 全文未逐行实读（仅按需引用具体断言行）；`test_interview.py` 仅读 L1-80。
  2. `client/pages/portrait/manage.uvue` L462-849（纯样式段）未读 —— 不影响结论（D07-7/22 均落在 L200-460 逻辑段）。
  3. `client/utils/api.uts` / `auth.uts` / `queue_store.uts` 未实读（本域仅间接依赖，未发现疑点）。
  4. `backend/app/services/rag/*` 未全文实读，仅按 `content_class` 检索确认其为 D07-4 的受影响消费方。
  5. D07-1 未实际构建镜像运行时复现（静态路径推导，已在条目内标注）。
  6. 本域与 `agent/` 目录的交互未审（任务明确排除 `agent/`）。
