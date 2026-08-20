# 模型资产清单（backend/models + HF 缓存）— 2026-08-20 首版

> 用途：防重复下载 / 快速判断可删性 / 新 Agent 上岗速查。
> 维护规则：新增模型必须登记（名称/版本/下载源/大小/加载代码/可删性）；下载前先查本清单 + 本地缓存。
> 更新：2026-08-20（清理后核对），核对人：Agent + 用户确认。

---

## 一、在用模型（禁止删除）

### 1. BGE-M3 文本塔（dense+sparse 三合一）

| 项 | 值 |
|---|---|
| 位置 | HF 缓存 `~/.cache/huggingface/hub/models--BAAI--bge-m3/` |
| 在用版本 | snapshot `5617a9f61b028005a4858fdac845db406aefb181`（pytorch_model.bin 格式，refs/main 指向） |
| 大小 | 2.2GB（实体复制；blobs 中另有缓存） |
| 下载源 | huggingface.co → 实际走 hf-mirror.com（本机 HF 不可达） |
| 加载代码 | `backend/app/services/embedding.py`（SentenceTransformer + fp16 + HF_HUB_OFFLINE=1） |
| 用途 | 全部 RAG 检索 / NER 无关 / 图片 caption 向量化 / SetFit 底座 |
| 可删性 | ❌ 不可删（RAG 主塔） |

### 2. bge-reranker-base 精排

| 项 | 值 |
|---|---|
| 位置 | `backend/models/bge-reranker-base/`（3.2GB，含 onnx） |
| 加载代码 | `backend/app/services/rerank.py`（CrossEncoder，settings.reranker_model） |
| 用途 | 双层 Rerank 第一层粗排 |
| 可删性 | ❌ 不可删（当前生效）⚠️ 可换：设计指定 bge-reranker-v2-m3，已下载待切换 |

### 3. bge-reranker-v2-m3（待切换，已下载）

| 项 | 值 |
|---|---|
| 位置 | `backend/models/bge-reranker-v2-m3/`（2.2GB） |
| 状态 | ⚠️ 已下载未接线（设计指定版本，B2 差距项 P1） |
| 用途 | 替换 base 做粗排（切换：改 settings.reranker_model 后重测 hit_rate） |
| 可删性 | ⚠️ 若决定继续用 base 可删；建议保留待切换 |

### 4. setfit-classifier（F2 分类）

| 项 | 值 |
|---|---|
| 位置 | `backend/models/setfit-classifier/`（2.2GB，底座 BAAI/bge-m3） |
| 加载代码 | `backend/app/services/classifier.py`（SetFitModel.from_pretrained） |
| 用途 | 文字碎片 5 类分类（F2）+ 纠错三层裁决第②层 |
| 可删性 | ❌ 不可删（可重建：`backend/scripts/train_setfit.py` 种子 50 条再训） |

---

## 二、可删模型（历史残留，代码零引用）

### 5. grounding-dino-base（HF 缓存 1.8GB）——**建议删**

| 项 | 值 |
|---|---|
| 位置 | HF 缓存 `~/.cache/huggingface/hub/models--IDEA-Research--grounding-dino-base/` |
| 引用 | 代码/文档零引用（历史试验：疑似早期图像定位尝试，未落地） |
| 可删性 | 🗑️ 已删（2026-08-20，释放 1.8GB） |

### 6. bert-base-uncased（HF 缓存 841MB）——**建议删**

| 项 | 值 |
|---|---|
| 位置 | `~/.cache/huggingface/hub/models--bert-base-uncased/` |
| 引用 | 零引用（SetFit 底座实为 bge-m3，非 bert；疑似早期实验） |
| 可删性 | 🗑️ 已删（2026-08-20，释放 841MB） |

### 7. Qwen2.5-1.5B-Instruct / GGUF（HF 缓存 0MB）——**可删（空壳）**

| 项 | 值 |
|---|---|
| 位置 | `~/.cache/huggingface/hub/models--Qwen--Qwen2.5-1.5B-Instruct*/` |
| 状态 | 空壳（0MB，仅元数据/refs，无权重）——下载未完成或仅 HEAD 探测 |
| 引用 | 零引用（Windows 桌面端 Ollama 备选未来才需要，届时重新下载） |
| 可删性 | 🗑️ 已删（2026-08-20，空壳目录） |

### 8. bge-reranker-base（HF 缓存空壳 0MB）——**可删（空壳）**

| 项 | 值 |
|---|---|
| 位置 | `~/.cache/huggingface/hub/models--BAAI--bge-reranker-base/` |
| 状态 | 0MB 空壳（实际模型在 backend/models/） |
| 可删性 | 🗑️ 已删（2026-08-20，空壳目录） |

---

## 三、bge-m3 HF 缓存内部结构（已清理项留档）

| 路径 | 大小 | 状态 |
|---|---|---|
| `snapshots/5617a9f6...` | 2.2GB | ✅ 在用（保留） |
| `snapshots/9a0624b8...` | 2.2GB | 🗑️ 已删（2026-08-20：旧 safetensors 版残留，refs 未指向） |
| `blobs/*.incomplete` | 1.65GB | 🗑️ 已删（2026-08-20：下载中断残留） |

> 教训（AGENTS.md #18-22）：HF hub Windows 下 snapshots 为实体复制（非软链）→ blobs+snapshots 双份；
> sentence-transformers 版本差异会拉不同权重格式（pytorch_model.bin vs model.safetensors）各留一个 snapshot；
> `.incomplete` = 中断残留可删；判断在用版本看 `refs/main`。

---

## 四、其他下载资产（非模型）

| 资产 | 位置 | 大小 | 状态 |
|---|---|---|---|
| AISHELL-1 语料（10 说话人） | `research/asr_bench/wav/` | 310MB | 🔒 已 gitignore（可重新下载 hf-mirror `AISHELL/AISHELL-1`） |
| AISHELL 标注 | `research/asr_bench/aishell_transcript_v0.8.txt` | 9.6MB | ✅ 已入库 |
| WER 报告 | `research/asr_bench/wer_report.json` | — | ✅ 已入库 |

---

## 五、下载源速查（本机网络）

1. huggingface.co 不可达 → 一律 hf-mirror.com 或 modelscope.cn
2. hf-mirror 镜像：`hf-mirror.com/BAAI/bge-m3`（与 HF 同路径）
3. 下载前检查：`~/.cache/huggingface/hub/models--<org>--<name>/` 是否已有完整权重（refs/main + 权重文件非 .incomplete）
4. 新模型落地：登记本清单 + 在加载代码顶部注释指向本文件
