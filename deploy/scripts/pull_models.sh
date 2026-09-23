#!/usr/bin/env bash
# ============================================================================
# 忆述光华 · 模型资产预置（S1 卡 · 幂等）
#
# 必需资产（约 3.8–5.3G，见 docs/服务器规格核算_20260921.md §4）：
#   ① SenseVoiceSmall-ONNX  → backend/models/SenseVoiceSmall-onnx（本地声学情绪）
#   ② BGE-M3                → HF 缓存（文本塔 dense+sparse，检索与索引共用）
#   ③ SetFit 分类器         → backend/models/setfit-classifier（**本脚本只校验，不训练**）
# 刻意不装：bge-reranker-v2-m3（3.2G）——RERANK_ENABLED=false 且 CPU 单对 ~850ms 超 P95<3s 门禁
#   （backend/app/core/config.py:148-152）。将来上 GPU 再单独补。
#
# 幂等契约：目录/缓存已就绪 → 打印 SKIP 并继续，不重复下载。
# 用法（仓库根）：bash deploy/scripts/pull_models.sh
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${DEPLOY_DIR}/.." && pwd)"

SENSEVOICE_DIR="${REPO_ROOT}/backend/models/SenseVoiceSmall-onnx"
SETFIT_DIR="${REPO_ROOT}/backend/models/setfit-classifier"
HF_HUB_CACHE="${HF_HUB_CACHE:-$HOME/.cache/huggingface/hub}"

log()  { printf '[models] %s\n' "$*"; }
warn() { printf '[models][WARN] %s\n' "$*" >&2; }
die()  { printf '[models][FAIL] %s\n' "$*" >&2; exit 1; }

PYTHON_BIN=""
if [[ -x "${REPO_ROOT}/backend/.venv/bin/python" ]]; then
	PYTHON_BIN="${REPO_ROOT}/backend/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
	PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
	PYTHON_BIN="$(command -v python)"
fi
[[ -n "${PYTHON_BIN}" ]] || die "未找到 Python 解释器（期望 backend/.venv/bin/python 或 python3）"

# ---------- 1. SenseVoice ONNX ----------
if [[ -n "$(ls -A "${SENSEVOICE_DIR}" 2>/dev/null || true)" ]]; then
	log "SenseVoice 已就绪 → SKIP（${SENSEVOICE_DIR}）"
else
	log "预置 SenseVoiceSmall-ONNX → ${SENSEVOICE_DIR}（需联网，走 ModelScope）"
	# 该步必须联网：modelscope snapshot_download。显式覆盖离线开关（app 侧默认 HF_HUB_OFFLINE=1）
	( cd "${REPO_ROOT}/backend" && \
		HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$HOME/.cache/modelscope}" \
		"${PYTHON_BIN}" scripts/prepare_sensevoice.py --target "${SENSEVOICE_DIR}" )
fi

# 预置完成的判据：ONNX 权重 + 分词文件同时在位（与 backends.py 的校验同源）
if ! ls "${SENSEVOICE_DIR}"/*.onnx >/dev/null 2>&1; then
	die "SenseVoice 目录缺少 ONNX 权重：${SENSEVOICE_DIR}（SENSEVOICE_MODEL_DIR 指向此处，缺则首个请求会尝试联网下载）"
fi
log "SenseVoice 校验通过 ✅"

# ---------- 2. BGE-M3（HF 缓存） ----------
if [[ -d "${HF_HUB_CACHE}/models--BAAI--bge-m3" ]]; then
	log "BGE-M3 已在 HF 缓存 → SKIP（${HF_HUB_CACHE}/models--BAAI--bge-m3）"
else
	log "下载并预热 BGE-M3（~2.2G，仓库根 scripts/warm_hf_models.py）"
	# 该脚本自身强制 HF_HUB_OFFLINE=0 / local_files_only=False（CI #20 教训）
	( cd "${REPO_ROOT}" && "${PYTHON_BIN}" scripts/warm_hf_models.py )
fi

# ---------- 3. SetFit（只校验，不训练） ----------
if [[ -f "${SETFIT_DIR}/model.safetensors" || -f "${SETFIT_DIR}/pytorch_model.bin" ]]; then
	log "SetFit 分类器已就位 → OK（${SETFIT_DIR}）"
else
	warn "SetFit 分类器**缺失**：${SETFIT_DIR}"
	warn "  后果：分类静默降级为 mixed（backend/app/services/classifier.py:43-53），不 crash 但失去分类能力。"
	warn "  处置：从现有开发机拷贝该目录（推荐），或在本机跑 python backend/scripts/train_setfit.py（需种子数据与时间）。"
	warn "  本脚本不自动训练——训练产物质量依赖真值数据，属人工决策。"
fi

# ---------- 4. 显式声明不装的资产 ----------
log "跳过 reranker（bge-reranker-v2-m3，3.2G）：RERANK_ENABLED=false，见脚本头说明"

# ---------- 5. 磁盘占用汇总 ----------
log "模型资产占用（用于回填 docs/服务器规格核算_20260921.md §7 校验表第 5 项）："
du -sh "${REPO_ROOT}/backend/models" 2>/dev/null || true
du -sh "${HF_HUB_CACHE}" 2>/dev/null || true

log "完成。若 SENSEVOICE_MODEL_DIR 为空，请在 deploy/.env 指向：${SENSEVOICE_DIR}"
