# 照片管线合并进 YSGH-APP（忆述光华）实施计划

> 版本：v3.0（2026-09-03）｜状态：范围已收敛，待开工
>
> 决策依据（用户已拍板）：
> 1. 采用 **37 表** 数据库结构（`忆述光华_数据库表结构(3).docx`——与 2026-08-27 所发为同一文件，内容未变）
> 2. 图片向量走 **Qwen3-VL caption → BGE-M3 1024**
> 3. **Qdrant 用本机 Docker**
> 4. 照片去重用 **imagehash 库的 pHash**
> 5. 方案 = **合并**（photo_pipeline 能力并入 YSGH-APP 主仓库，非桥接）
> 6. 代码库：`https://github.com/zqhmy1234/YSGH-APP`，基线 = **develop@cea5025**（GitHub main 仍是 3869111 旧快照，不作为基线）
>
> 负责人三问答复（2026-09-03，本版据此收敛范围）：
> - **部署目标：先在自己电脑上跑** → 阶段 0 只做本机 docker-compose（4 个现成服务），后端本地 uvicorn 直接跑，**本轮不打包后端镜像**；
> - **上游 fix/4b 分支：不做处理** → 基线保持 develop@cea5025 现状；若上游后续合入，再相应跟进；
> - **相册来源：目前只保留 Android 一条路径** → 产品路径以上游 yishu-photo-watch（相册监听→自动上传）为准，**本轮不做 Windows/目录导入，不做多来源统一格式层**；验收用一次性测试脚本灌入照片（测试工具，非产品功能）。

---

## 一、结论摘要（先说结果）

**上游 develop 已把原来计划里的部分活干完**：EXIF 时间解析、缩略图、照片注册链路的稳定性加固、Qwen3-VL→BGE-M3 真实链路、事件聚合增强、pgvector 扩展、Android 相册监听自动上传，均已有实现与测试。

**本轮真正要补的缺口收敛为 5 项：**

1. **数据库 37 表对齐**：新增 `files`、`content_chunks`；`contents` 增 `file_id / raw_text / normalized_text / summary / summary_strategy / image_phash`，`qdrant_text_id/qdrant_image_id` 更名 `text_vector_id/image_vector_id`；唯一约束拆为 `normalized_text_hash` + `image_phash`。上游 schema 仍是 `perceptual_hash` + `qdrant_*_id`。
2. **OCR 服务**：全仓无任何 OCR 调用，BAIDU key 仅为占位 → 迁入 photo_pipeline 百度高精度 OCR（2000px 压缩防 216205），落 `raw_text`。
3. **imagehash pHash 服务端去重**：上游去重仍靠客户端传 `perceptual_hash` 字符串 → 服务端计算 pHash，撞同用户重复照片直接跳过（省 OCR/caption 计费）。
4. **本机 Docker 编排**：仓库仍无 docker-compose，团队手工起容器 → 一条命令起齐 PostgreSQL/Redis/Qdrant/MinIO（本机开发用，不打包后端镜像）。
5. **MinIO 2 桶**：上游仍单桶 `yishu-photos` → 按文档拆 `originals` + `thumbnails`（D2 决策）。

**明确不做（本轮范围外）**：Windows/目录相册导入、多来源统一格式层、fix/4b 合并、后端镜像打包、客户端 UI 相关。

**不迁移（被替代）的 photo_pipeline 组件**：`clip_service.py`（Chinese-CLIP → BGE-M3）、`event_service.py`（→ ST-DBSCAN）、`db_milvus/db_mysql`（→ PostgreSQL+Qdrant）、`main.py` 路由（→ YSGH-APP API）、`sync_album`（本轮不迁，Android 已有监听路径）。

---

## 二、基线说明（防混淆，重要）

- 上游主开发线 = **`develop`**；`main` 是 2026-08-27 旧快照。
- 我们以 **`develop@cea5025`** 为开工基线。
- **fix/4b 不做处理**：其含 `services/exif.py` 干净版等，但不合并。EXIF 调用以 develop 现有实现（`api/contents.py` 内 `_extract_exif_datetime`）为准；若上游日后合入 fix/4b，再做调用点切换（登记为跟进项，不阻塞本轮）。
- 上游 develop 高频迭代：开工时以实际合入/拉取到的 HEAD 为准；本计划文件中的 commit 号仅作记录。

---

## 三、37 表与现状差异对照

### 3.1 新增 2 表

| 表 | 关键列 | 本轮用途 |
|---|---|---|
| `files` | id, user_id, filename, mime_type, file_size, extension, storage_key(`{user_id}/{file_id}/original`), file_hash(**SHA256**), status, parser, parser_version, error_message | 文件原始层，contents.file_id 指向它；file_hash 文件级精确去重 |
| `content_chunks` | id, content_id, chunk_index, text, char_start, char_end, token_count, chunk_type, metadata | RAG 切块层；D5 已确认本轮建表但切块默认关闭 |

### 3.2 contents 列变更

| 列 | 现状（develop） | 37 表目标 | 说明 |
|---|---|---|---|
| perceptual_hash | 有（客户端传） | 拆分为 `normalized_text_hash` + `image_phash` | 文本指纹（SHA256 归一化文本）+ 图片 pHash 分开；存量迁移规则：perceptual_hash → image_phash，normalized_text_hash 首轮置空 |
| qdrant_text_id | 有 | 更名 `text_vector_id` | 仅改名 |
| qdrant_image_id | 有 | 更名 `image_vector_id` | 仅改名 |
| file_id | 无 | 新增，FK files.id | 照片必须挂文件 |
| raw_text | 无 | 新增 | OCR 原文 |
| normalized_text | 无 | 新增 | 归一化文本（去重用） |
| summary / summary_strategy | 无 | 新增 | 摘要列（D5：默认 passthrough，不启用 LLM 摘要） |
| text / cos_key / thumbnail_key | 有 | 保留 | — |

### 3.3 其他差异

| 表 | 差异 | 处理 |
|---|---|---|
| `correction_log` | 文档：`content_embedding vector(1024)`（pgvector）；现状：`qdrant_point_id text` | D5 已确认切 pgvector；pgvector 扩展上游已启用，只需加列+迁移 |
| `user_wechat_bindings` | 文档表格出现 `file_id` 列 | D6 已确认不加（文档笔误） |
| Qdrant 集合 | 文档：2 集合；现状：1 集合 named vectors | D1 已确认保留 1 集合（零改动） |
| MinIO 桶 | 文档：2 桶；现状：单桶 | D2 已确认 2 桶，属本轮工作 |

---

## 四、实施步骤（每步做什么 + 具体效果）

### 阶段 0：本机 Docker 基础设施（半天）

**做什么**：仓库根新增 `docker-compose.yml`（4 服务）：
- postgres:16（含 pgvector）、redis:7、qdrant、minio（自动建 `originals` + `thumbnails` 两桶）；
- `backend/.env.example` 补 QDRANT_URL / MINIO_* 双桶配置；
- README 补"一条命令起全部服务"。

**边界（本机跑）**：后端代码本地 `uvicorn` 运行，**不写 Dockerfile、不打后端镜像**；四个中间件用官方镜像即可。

**具体效果**：`docker compose up -d` 一键起齐；Docker 重启后幂等拉回；本机即跑真实 MinIO 双桶链路。

**验证**：`docker compose ps` 全 healthy；Qdrant collection 存在；MinIO 控制台见 2 桶。

---

### 阶段 1：数据库对齐 37 表（1 天）

**做什么**：
1. 更新 `backend/sql/schema.sql` 与 ORM（models/ 子包）：新增 `File`、`ContentChunk`；`Content` 增列/更名（3.2 对照）；`correction_log` 按 D5 加 `content_embedding vector(1024)`。
2. 写 Alembic 迁移 `v4_37tables.py`（可回滚；保留 upload_tasks/upload_chunks 与存量数据；perceptual_hash → image_phash 迁移规则）。
3. 去重约束：`UNIQUE(user_id, normalized_text_hash) WHERE ... IS NOT NULL` + `UNIQUE(user_id, image_phash) WHERE ... IS NOT NULL`（替换原 perceptual_hash 唯一）。

**具体效果**：`alembic upgrade head` 后物理表数 = 39（37 业务表 + 2 上传基础设施表）；存量 contents 无损。

**验证**：`scripts/check_schema_drift.py`（上游已有）+ 新增 37 表齐全断言。

---

### 阶段 2：照片处理链路收口（核心，1.5–2 天）

**上游已做，本轮不重复**：EXIF 解析（api/contents.py，fix/4b 不处理）、缩略图、photo_content.py 注册口、Qwen3-VL caption 真实链路、事件聚合、Android 相册监听上传。

**本轮只补**：
1. **OCR 接入**：photo_pipeline `ocr_service.py` → `backend/app/services/external/ocr.py`（百度高精度 + 2000px 压缩 + token 缓存 + 重试）；`pipeline._process_photo` 增加 OCR 步骤：
   ```
   ① OCR → contents.raw_text
   ② Qwen3-VL caption（已有）→ contents.text（OCR 空时用 caption；都有则 text=OCR+caption）
   ③ normalized_text = 归一化(raw_text/text) → normalized_text_hash
   ④ BGE-M3(caption) → image_vec（已有）；BGE-M3(text) → text_vec（已有）
   ⑤ files/contents 落库（status=done）
   ```
2. **pHash 服务端去重**：新增依赖 `imagehash`；在 `photo_content.register_photo_content` 去重分支接入服务端 pHash（与客户端 perceptual_hash 兼容：服务端 image_phash 优先，客户端哈希兜底），撞同用户已有 → 409/跳过。
3. **上传接口对齐**：photo 分支落 files 表 → 算 pHash → 建 contents。

**具体效果**：单张照片全链路（上传→原件/缩略图入 MinIO 双桶→OCR→caption→双向量→事件聚合）；重复照片靠 pHash 跳过，不重复计费。

**验证**：新增 `tests/test_photo_pipeline.py`：photo_pipeline `test_images/` 20 张冒烟（OCR 非空率、pHash 命中、向量可检索、事件产出 L1）。

---

### 阶段 3：端到端验收（1 天）

**做什么**：
1. 写**一次性测试灌入脚本**（`scripts/import_photos_for_acceptance.py`，仅测试工具，不进产品路径）：遍历本地照片目录 → 调上传接口批量入库（复用阶段 2 链路；Android 相册监听在真机上另有验收）。
2. 用 150/205 张真实照片灌入，记录指标：导入成功率 ≥95%、pHash 去重识别、OCR 可用率、事件数合理性、P95 ≤3s、以图搜图 Top3 命中。
3. 更新 feature_list/progress，跑 review_agent 全绿后提交。

**说明**：Android 端"相册→自动导入"链路由上游 yishu-photo-watch 承担，本阶段在电脑上用测试脚本验证的是**后端处理链**与 37 表/OCR/pHash 的效果。

---

## 五、决策与范围确认记录（2026-09-03）

| # | 事项 | 结论 |
|---|---|---|
| D1 | Qdrant 集合形态 | 保留 1 集合 named vectors（上游现状一致） |
| D2 | MinIO 桶 | originals + thumbnails 2 桶（本轮做） |
| D3 | 照片 GPS | 无 GPS 只走时间窗（上游已按此处理） |
| D4 | 证件类敏感拦截 | 不迁入 CLIP，只用文本护栏（上游现状一致） |
| D5 | content_chunks/summary/correction_log | 建表但切块/摘要关；correction_log 切 pgvector |
| D6 | user_wechat_bindings.file_id | 不加（文档笔误） |
| S1 | 部署目标 | 先本机跑；本轮不打后端镜像 |
| S2 | fix/4b 分支 | 不做处理；上游后续合入再跟进 |
| S3 | 相册导入范围 | 仅 Android 路径（上游已有）；不做 Windows 导入/统一格式层 |

---

## 六、需要你提供的（动工前）

1. `BAIDU_API_KEY` / `BAIDU_SECRET_KEY`（OCR 用；`backend/.env` 已有占位，你之前说"密钥等会填"）
2. `DASHSCOPE_API_KEY`（Qwen3-VL caption；若上游/负责人已配置到 `.env` 或 Infisical 则免）
3. 验收照片目录路径（150/205 张，阶段 3 用；你之前确认提供 150 张真实相册导出）

---

## 七、风险与说明

- **基线漂移**：上游 develop 高频迭代，开工时以实际拉取 HEAD 为准。
- **fix/4b 不处理**：EXIF 以 develop 现有实现为准；若上游合入 fix/4b，需切换调用点（跟进项）。
- **脏树历史**：上游 D-02/D-03 曾只在脏工作树 → 验证"上游已实现"以合入 develop 的代码为准，必要时复跑测试。
- **旧数据不迁移**：photo_pipeline 的 SQLite（197 资产/39 事件）不迁入 PostgreSQL，本轮从 YSGH-APP 全新导入。
- **密钥纪律**：外部 key 只经 Infisical 或 `backend/.env`（已 .gitignore），不写进代码与提交。