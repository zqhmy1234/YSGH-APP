# 照片管线 MVP —— 可执行整理版

> 由 `photo-pipeline-mvp-ops-guide.md` 整理而成：把每一步压缩成 **操作 → 指令 → 代码 → 验证**，并针对当前机器环境做了适配标注。
> 项目根目录就是本工作区 `D:\projects\photo_pipeline`（指南里假设的 `photo_pipeline/` 子目录已不需要再建）。

---

## 0. 开工前：现状核对（2026-08-18 实测）

| 项目 | 状态 | 说明 |
|---|---|---|
| `venv` 虚拟环境 | ✅ 已有 | Python 3.14.4，依赖已全部安装 |
| `requirements.txt` | ✅ 已有 | 是 pip freeze 快照（已装版本清单），可直接用于复现 |
| `test.py` / `milvus_test.py` | ✅ 已有 | MinIO、Milvus 连通性测试脚本 |
| `baidu.env` | ⚠️ 空文件 | 百度密钥尚未填写 |
| `.env` / `.gitignore` | ❌ 未建 | 见步骤 0.4 |
| 项目骨架（`main.py` 等） | ❌ 未建 | 见步骤 1 |

### 与指南的版本差异（重要，先读）

| 项 | 指南版本 | 本机实际 | 影响与处理 |
|---|---|---|---|
| Python | 3.10 ~ 3.12 | 3.14.4 | 依赖已装好且可导入，先用；若装新依赖失败，再退回 3.12 |
| pymilvus | 2.4 ~ 2.5 | 3.0.1 | `create_collection` 等签名兼容；Windows 仍不支持 Milvus Lite，**用 numpy 后端** |
  | transformers | 4.x | 5.15.0 | 中文 CLIP 须用内置 `ChineseCLIPModel`/`ChineseCLIPProcessor` 加载（见 3.2） |
| torch | ≥ 2.2 | 2.13.0+cpu | CPU 版，够用；有 NVIDIA 卡可重装 CUDA 版 |

### 0.1 激活虚拟环境（后续所有命令都依赖它）

```powershell
cd D:\projects\photo_pipeline
venv\Scripts\Activate
```

命令提示符前出现 `(venv)` 即成功。不激活的话，把下面所有 `python` 替换成 `venv\Scripts\python.exe`。

```powershell
python --version   # 应为 3.14.4
python -c "import fastapi, requests, dotenv, PIL, torch, transformers, pymilvus; print('依赖 OK')"
```

> 依赖已装好，指南 0.3 的安装步骤可跳过；如果哪天需要重装，用指南里的 `requirements.txt` 内容或本机 `requirements.txt` 执行 `pip install -r requirements.txt`。

### 0.2 创建 `.env`（密钥，绝不提交）

```powershell
New-Item -ItemType File -Force .env
```

`.env` 内容：

```text
# ---------- 百度 OCR ----------
BAIDU_API_KEY=在这里填你的API_Key
BAIDU_SECRET_KEY=在这里填你的Secret_Key
# 已按技术方案拍板切换到高精度版（印刷体 99.5%+，按次计费）
BAIDU_OCR_URL=https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic
BAIDU_OCR_LANGUAGE_TYPE=CHN_ENG
BAIDU_OCR_DETECT_DIRECTION=true

# ---------- 关系库：sqlite（本地） / mysql（上云时） ----------
DB_TYPE=sqlite
DB_PATH=photo_pipeline.db
# DB_TYPE=mysql 时填写：
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的数据库密码
MYSQL_DB=photo_assets

# ---------- 向量库：auto / milvus_lite / numpy ----------
# Windows 本机直接写 numpy，最省事；上服务器再改 milvus 地址
VECTOR_BACKEND=numpy
MILVUS_URI=milvus_data/milvus_local.db

# ---------- 文件存储 ----------
STORAGE_DIR=uploads
MAX_FILE_MB=10
# 隐私红线：云端不存原图，OCR 成功后立即删除
KEEP_ORIGINAL=false

# ---------- 模型 ----------
# 中文场景推荐 Chinese-CLIP，英文/通用场景用 openai/clip-vit-base-patch32
CLIP_MODEL=OFA-Sys/chinese-clip-vit-base-patch16
```

### 0.3 创建 `.gitignore`

```powershell
New-Item -ItemType File -Force .gitignore
```

内容：

```text
.venv/
venv/
__pycache__/
*.pyc
.env
uploads/
milvus_data/
photo_pipeline.db
eval_data/
test_images/
```

---

## 步骤 1 项目骨架（约 5 分钟）

```powershell
New-Item -ItemType Directory -Force scripts, test_images
New-Item -ItemType File -Force main.py, ocr_service.py, clip_service.py, db_mysql.py, db_milvus.py, storage.py, run.py
```

各文件职责：

| 文件 | 职责 |
|---|---|
| `main.py` | FastAPI 入口，`/upload`、`/search`、`/health` |
| `ocr_service.py` | 百度 OCR：拿 token + 识别文字 |
| `clip_service.py` | CLIP：图片/文字 → 512 维向量 |
| `db_mysql.py` | 元数据入库（默认 SQLite，可切 MySQL） |
| `db_milvus.py` | 向量入库与检索（Milvus / numpy 简易后端） |
| `storage.py` | 图片文件保存/删除 |
| `run.py` | 一键启动服务 |
| `scripts/` | 测试与评估脚本 |

骨架先建空文件，内容在下面各步骤逐个填充。

---

## 步骤 2 跑通百度 OCR（约 30 分钟）

### 2.1 拿密钥

1. 打开[百度智能云控制台](https://console.bce.baidu.com/)并登录。
2. 搜索"文字识别" → 进入产品页 → "创建应用"（或"立即使用"）。
3. 记下 **API Key** 和 **Secret Key**，填入 `.env`。

> 免费额度开发期够用，别写死循环疯狂调用。

### 2.2 `ocr_service.py`

```python
import base64
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
OCR_URL = os.getenv("BAIDU_OCR_URL", "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic")
OCR_LANGUAGE = os.getenv("BAIDU_OCR_LANGUAGE_TYPE", "CHN_ENG")
OCR_DETECT_DIRECTION = os.getenv("BAIDU_OCR_DETECT_DIRECTION", "true").lower() == "true"

_token_cache = {"token": None, "expire_at": 0}


def get_access_token() -> str:
    """获取百度 access_token，带缓存，过期前自动刷新。"""
    api_key = os.getenv("BAIDU_API_KEY", "").strip()
    secret_key = os.getenv("BAIDU_SECRET_KEY", "").strip()
    if not api_key or "在这里填" in api_key:
        raise RuntimeError("未配置 BAIDU_API_KEY：请在 .env 中填写百度智能云应用的 API Key")
    if not secret_key or "在这里填" in secret_key:
        raise RuntimeError("未配置 BAIDU_SECRET_KEY：请在 .env 中填写百度智能云应用的 Secret Key")

    now = time.time()
    if _token_cache["token"] and now < _token_cache["expire_at"] - 600:
        return _token_cache["token"]

    params = {
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": secret_key,
    }
    resp = requests.get(TOKEN_URL, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expire_at"] = now + data.get("expires_in", 2592000)  # 30 天
    return _token_cache["token"]


def ocr_image(image_bytes: bytes) -> str:
    """传入图片字节，返回识别出的文字（按行拼接）。"""
    params = {"access_token": get_access_token()}
    payload = {
        "image": base64.b64encode(image_bytes).decode("utf-8"),
        "language_type": OCR_LANGUAGE,
        "detect_direction": "true" if OCR_DETECT_DIRECTION else "false",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    resp = requests.post(OCR_URL, params=params, data=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "error_code" in data:
        raise RuntimeError(
            f"百度OCR错误 {data.get('error_code')}: {data.get('error_msg')}"
        )

    words = [item["words"] for item in data.get("words_result", [])]
    return "\n".join(words)
```

### 2.3 `scripts/test_ocr.py` + 验证

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ocr_service import ocr_image

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "test_images/sample.jpg"
    with open(path, "rb") as f:
        text = ocr_image(f.read())
    print("识别结果：")
    print(text)
```

准备一张含文字的测试图放到 `test_images/`，运行：

```powershell
python scripts/test_ocr.py test_images\sample.jpg
```

**验证点**：能打印出图片中的文字，第 2 步完成。

错误码速查：

| 错误码 | 含义 | 处理 |
|---|---|---|
| `216200` | token 无效或过期 | 检查密钥，程序会自动重新获取 |
| `17` | 当日免费额度用完 | 等次日或开通付费 |
| `18` | QPS 超限 | 调用间加 `time.sleep(0.5)` |
| `19` | 总请求量超限 | 检查计费/额度 |

---

## 步骤 3 跑通 CLIP 向量化（30–60 分钟，含模型下载）

### 3.1 模型说明

- `openai/clip-vit-base-patch32`：英文图文模型，512 维。
- `OFA-Sys/chinese-clip-vit-base-patch16`：中文图文模型（Chinese-CLIP），512 维，**中文场景推荐**。

首次加载模型约 718MB；默认从本地 `models/chinese-clip-vit-base-patch16` 加载，不联网。

### 3.2 `clip_service.py`

```python
import io
import os

import torch
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

MODEL_NAME = os.getenv(
    "CLIP_MODEL",
    "OFA-Sys/chinese-clip-vit-base-patch16"
)

_model = None
_processor = None


def _get_model():
    """懒加载：只有第一次调用时才真正加载模型（约 718MB，只加载一次）。"""
    global _model, _processor
    if _model is None:
        from transformers import ChineseCLIPModel, ChineseCLIPProcessor

        _model = ChineseCLIPModel.from_pretrained(MODEL_NAME)
        _processor = ChineseCLIPProcessor.from_pretrained(MODEL_NAME)
        _model.eval()
    return _model, _processor


def _normalize(features):
    return features / features.norm(dim=-1, keepdim=True)


def embed_image(image_bytes: bytes) -> list:
    """图片 → 归一化后的 512 维向量。"""
    model, processor = _get_model()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    inputs = processor(images=image, return_tensors="pt")
    with torch.no_grad():
        outputs = model.get_image_features(**inputs)
        feats = _normalize(outputs.pooler_output)
    return feats[0].tolist()


def embed_text(text: str) -> list:
    """文字 → 归一化后的 512 维向量（用于以文搜图）。"""
    model, processor = _get_model()
    inputs = processor(text=text, return_tensors="pt")
    with torch.no_grad():
        outputs = model.get_text_features(**inputs)
        feats = _normalize(outputs.pooler_output)
    return feats[0].tolist()
```

说明：
- 向量先归一化，余弦相似度与内积等价，Milvus 选 `COSINE` 或 `IP` 都行。
- `OFA-Sys/chinese-clip-*` 的 config 声明 `ChineseCLIPModel` 架构，必须用内置的 `ChineseCLIPModel`/`ChineseCLIPProcessor` 加载；`get_image_features`/`get_text_features` 返回带 `pooler_output` 的输出对象。若换 OpenAI 英文模型 `openai/clip-vit-base-patch32`，则改回 `CLIPModel`/`CLIPProcessor` 并直接用返回值归一化。

### 3.3 `scripts/test_clip.py` + 验证

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clip_service import embed_image

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "test_images/sample.jpg"
    with open(path, "rb") as f:
        vec = embed_image(f.read())
    print("向量维度:", len(vec))
    print("前 5 个数值:", vec[:5])
    assert len(vec) == 512, "向量维度不是 512，请检查模型"
    print("维度验证通过 [PASS]")
```

```powershell
python scripts/test_clip.py test_images\sample.jpg
```

**验证点**：输出"向量维度: 512"，数值在 -1 到 1 之间。

### 3.4 模型下载慢/失败

```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"
```

或手动把模型文件下载到 `C:\Users\ZLY20\.cache\huggingface\hub\models--OFA-Sys--chinese-clip-vit-base-patch16`。

---

## 步骤 4 接入存储（约 60 分钟）

### 4.1 `db_mysql.py`（SQLite 默认，MySQL 可选）

```python
import os
import sqlite3

from dotenv import load_dotenv

load_dotenv()

DB_TYPE = os.getenv("DB_TYPE", "sqlite")
DB_PATH = os.getenv("DB_PATH", "photo_pipeline.db")


def _sqlite_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _mysql_conn():
    import pymysql

    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", "photo_assets"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def init_db():
    """建表 photo_assets。SQLite 与 MySQL 各有一套建表语句。"""
    if DB_TYPE == "mysql":
        conn = _mysql_conn()
        conn.cursor().execute("""
            CREATE TABLE IF NOT EXISTS photo_assets (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(64),
                file_path VARCHAR(255),
                ocr_text TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
        conn.close()
    else:
        conn = _sqlite_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS photo_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                file_path TEXT,
                ocr_text TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.commit()
        conn.close()


def save_asset(user_id: str, file_path: str, ocr_text: str) -> int:
    """插入一条资产记录，返回自增 id。"""
    if DB_TYPE == "mysql":
        conn = _mysql_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO photo_assets (user_id, file_path, ocr_text) VALUES (%s, %s, %s)",
            (user_id, file_path, ocr_text),
        )
        asset_id = cur.lastrowid
        conn.commit()
        conn.close()
    else:
        conn = _sqlite_conn()
        cur = conn.execute(
            "INSERT INTO photo_assets (user_id, file_path, ocr_text) VALUES (?, ?, ?)",
            (user_id, file_path, ocr_text),
        )
        asset_id = cur.lastrowid
        conn.commit()
        conn.close()
    return asset_id


def get_asset(asset_id: int):
    """按 id 查询，返回 dict。"""
    if DB_TYPE == "mysql":
        conn = _mysql_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM photo_assets WHERE id = %s", (asset_id,))
        row = cur.fetchone()
        conn.close()
        return row
    else:
        conn = _sqlite_conn()
        cur = conn.execute("SELECT * FROM photo_assets WHERE id = ?", (asset_id,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None
```

验证：

```powershell
python -c "import db_mysql; db_mysql.init_db(); aid=db_mysql.save_asset('user1','uploads/test.jpg','你好世界'); print('asset_id =', aid); print(db_mysql.get_asset(aid))"
```

**验证点**：输出 `asset_id = 1` 和包含 `ocr_text` 的记录。

### 4.2 `db_milvus.py`（numpy 简易后端 / Milvus Lite）

```python
import os

import numpy as np
from dotenv import load_dotenv

load_dotenv()

COLLECTION = "photo_embeddings"
DIM = 512
BACKEND = os.getenv("VECTOR_BACKEND", "auto").lower()


class NumpyStore:
    """Windows 本机可用的简易向量后端，接口与 Milvus 保持一致。"""

    def __init__(self, data_path="milvus_data/vectors.npz"):
        self.path = data_path
        os.makedirs(os.path.dirname(data_path) or ".", exist_ok=True)
        self.asset_ids = []
        self.vectors = []
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            data = np.load(self.path, allow_pickle=True)
            self.asset_ids = list(data["asset_ids"])
            self.vectors = list(data["vectors"])

    def _save(self):
        np.savez(
            self.path,
            asset_ids=np.array(self.asset_ids),
            vectors=np.array(self.vectors, dtype=float),
        )

    def insert(self, asset_id, embedding):
        self.asset_ids.append(asset_id)
        self.vectors.append(embedding)
        self._save()

    def search(self, embedding, top_k=10):
        if not self.vectors:
            return []
        arr = np.array(self.vectors, dtype=float)
        q = np.array(embedding, dtype=float)
        q = q / (np.linalg.norm(q) + 1e-12)
        arr = arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12)
        scores = arr @ q
        idx = np.argsort(-scores)[:top_k]
        return [
            {
                "id": self.asset_ids[i],
                "distance": float(scores[i]),
                "entity": {"asset_id": self.asset_ids[i]},
            }
            for i in idx
        ]


class MilvusStore:
    """Milvus Lite / Milvus 服务器客户端。"""

    def __init__(self, uri):
        from pymilvus import MilvusClient

        self.client = MilvusClient(uri)
        if not self.client.has_collection(COLLECTION):
            self.client.create_collection(
                collection_name=COLLECTION,
                dimension=DIM,
                metric_type="COSINE",
            )

    def insert(self, asset_id, embedding):
        self.client.insert(
            COLLECTION,
            [{"asset_id": asset_id, "vector": embedding}],
        )

    def search(self, embedding, top_k=10):
        res = self.client.search(
            COLLECTION,
            data=[embedding],
            limit=top_k,
            output_fields=["asset_id"],
        )
        return res[0]


_store = None


def get_store():
    global _store
    if _store is None:
        uri = os.getenv("MILVUS_URI", "milvus_data/milvus_local.db")
        if BACKEND == "numpy":
            _store = NumpyStore()
        elif BACKEND == "milvus_lite":
            _store = MilvusStore(uri)
        else:  # auto
            try:
                _store = MilvusStore(uri)
                print("向量后端：Milvus Lite")
            except Exception as e:
                print(f"Milvus 不可用（{e}），切换到本地简易向量后端")
                _store = NumpyStore()
    return _store


def insert_embedding(asset_id: int, embedding: list):
    get_store().insert(asset_id, embedding)


def search_embedding(embedding: list, top_k: int = 10):
    return get_store().search(embedding, top_k=top_k)
```

`scripts/test_milvus.py`：

```python
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_milvus import insert_embedding, search_embedding

if __name__ == "__main__":
    vec = [random.random() for _ in range(512)]
    insert_embedding(1, vec)
    hits = search_embedding(vec, top_k=3)
    print("检索结果:", hits)
    assert hits and hits[0]["entity"]["asset_id"] == 1, "应能搜回刚插入的向量"
    print("向量库验证通过 [PASS]")
```

```powershell
python scripts\test_milvus.py
```

**验证点**：第一条命中是刚插入的 `asset_id = 1`，距离接近 1。

> 上服务器/云环境时，把 `.env` 的 `MILVUS_URI` 改成 Milvus 集群地址（如 `http://192.168.1.10:19530`），其余代码不动。

### 4.3 `storage.py`（本地文件夹，可换 MinIO）

```python
import datetime
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "uploads"))
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "10"))
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def save_image(data: bytes, filename: str) -> str:
    """保存图片到 uploads/年/月/日/uuid.扩展名，返回相对路径。"""
    ext = Path(filename or "upload.jpg").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"不支持的图片格式: {ext}")
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise ValueError(f"图片超过 {MAX_FILE_MB}MB 上限")

    today = datetime.date.today()
    folder = STORAGE_DIR / f"{today.year:04d}" / f"{today.month:02d}" / f"{today.day:02d}"
    folder.mkdir(parents=True, exist_ok=True)

    new_name = uuid.uuid4().hex + ext
    path = folder / new_name
    path.write_bytes(data)
    return path.as_posix()  # 统一用 / 分隔，跨平台一致


def delete_image(relative_path: str):
    """删除图片（隐私要求"不存原图"时用）。"""
    p = Path(relative_path)
    if p.exists():
        p.unlink()
```

验证：

```powershell
python -c "import storage; p=storage.save_image(b'fake-image-data','test.png'); print(p); storage.delete_image(p); print('OK')"
```

**验证点**：打印 `uploads/2026/08/18/一串字符.png`，随后文件被删除。

---

## 步骤 5 串成完整 Pipeline（约 60 分钟）

### 5.1 `main.py`

```python
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile

import db_milvus
import db_mysql
import storage
from clip_service import embed_image, embed_text
from ocr_service import ocr_image


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库和向量库（只执行一次）
    db_mysql.init_db()
    db_milvus.get_store()
    yield


app = FastAPI(title="Photo Pipeline MVP", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/upload")
async def upload(file: UploadFile = File(...), user_id: str = "default"):
    data = await file.read()

    # 1. 存文件
    try:
        rel_path = storage.save_image(data, file.filename or "upload.jpg")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    keep_original = os.getenv("KEEP_ORIGINAL", "true").lower() == "true"

    # 2. OCR
    try:
        text = ocr_image(data)
    except Exception as e:
        if not keep_original:
            storage.delete_image(rel_path)
        raise HTTPException(status_code=502, detail=f"OCR 失败: {e}")

    # 3. CLIP 向量化
    try:
        vector = embed_image(data)
    except Exception as e:
        if not keep_original:
            storage.delete_image(rel_path)
        raise HTTPException(status_code=500, detail=f"向量化失败: {e}")

    # 4. 隐私红线：云端不存原图时，识别完立即删除，后续任何失败都不留原图
    stored_path = rel_path
    if not keep_original:
        storage.delete_image(rel_path)
        stored_path = ""  # 元数据与响应都不再暴露已删除的原图路径

    # 5. 元数据 + 向量入库
    try:
        asset_id = db_mysql.save_asset(user_id, stored_path, text)
        db_milvus.insert_embedding(asset_id, vector)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"入库失败: {e}")

    return {
        "asset_id": asset_id,
        "file_path": stored_path,
        "ocr_text": text,
        "vector_dim": len(vector),
    }


@app.post("/search")
async def search(query: str, top_k: int = 10):
    """以文搜图：用文字向量检索最相似的图片。"""
    qvec = embed_text(query)
    hits = db_milvus.search_embedding(qvec, top_k=top_k)
    results = []
    for h in hits:
        asset = db_mysql.get_asset(h["entity"]["asset_id"])
        if asset:
            results.append(
                {
                    "asset_id": asset["id"],
                    "distance": h["distance"],
                    "ocr_text": asset["ocr_text"],
                }
            )
    return {"query": query, "results": results}
```

### 5.2 `run.py` + 启动

```python
import uvicorn

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
```

```powershell
python run.py
```

看到 `Uvicorn running on http://127.0.0.1:8000` 即成功。

### 5.3 命令行测试

```powershell
curl.exe -X POST -F "file=@test_images\sample.jpg" http://127.0.0.1:8000/upload
curl.exe -X POST "http://127.0.0.1:8000/search?query=你好&top_k=3"
```

预期返回包含 `asset_id`、`file_path`、`ocr_text`、`vector_dim: 512`。

### 5.4 Apifox 测试（可选）

1. 新建项目 → 新建请求：方法 `POST`，URL `http://127.0.0.1:8000/upload`。
2. Body 选 **form-data**，字段 `file` 类型选 **File**，选一张测试图。
3. 发送后看返回是否有 `asset_id`、`ocr_text`、`vector_dim: 512`。
4. 再测 `/search?query=任意文字`。

---

## 步骤 6 评估（约半天）

### 6.1 准备测试集

```powershell
New-Item -ItemType Directory -Force eval_data\images
```

`eval_data/ground_truth.csv`（UTF-8，第一行是表头）：

```csv
filename,text
img_001.jpg,你好世界这是测试文字
img_002.jpg,发票号码 12345678
img_003.jpg,第二行文字
```

标注规则：图里有什么字就抄什么，OCR 按行返回，标注按从上到下、从左到右的顺序写；手写体/艺术字难达标，结合图片类型解读结果。

### 6.2 `scripts/evaluate_cer.py`（目标 CER ≤ 2%）

```python
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rapidfuzz.distance import Levenshtein

from ocr_service import ocr_image


def normalize(s: str) -> str:
    """去掉所有空白，让比对不受换行/空格影响。"""
    return "".join(s.split())


def cer(pred: str, ref: str) -> float:
    p, r = normalize(pred), normalize(ref)
    if not r:
        return 0.0 if not p else 1.0
    return Levenshtein.distance(p, r) / len(r)


def main(csv_path: str = "eval_data/ground_truth.csv"):
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    total_err = 0.0
    total_chars = 0
    empty_count = 0
    fail_count = 0
    total = len(rows)
    for row in rows:
        img_path = "eval_data/images/" + row["filename"]
        with open(img_path, "rb") as f:
            data = f.read()
        try:
            pred = ocr_image(data)
        except Exception as e:
            fail_count += 1
            print(f"{row['filename']}: OCR 失败 - {e}")
            pred = ""
        if not normalize(pred):
            empty_count += 1
        e = cer(pred, row["text"])
        total_err += e * len(normalize(row["text"]))
        total_chars += len(normalize(row["text"]))
        print(f"{row['filename']}: CER = {e:.4f}")

    avg_cer = total_err / total_chars if total_chars else 0.0
    print(f"\n平均 CER = {avg_cer:.4f}（目标 ≤ 0.02）")
    print("达标 [PASS]" if avg_cer <= 0.02 else "未达标 [FAIL]")
    print(f"OCR 成功率 = {(total - fail_count) / total:.4f}（目标 ≥ 0.99）")
    print(f"空结果率 = {empty_count / total:.4f}（目标 ≤ 0.01）")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "eval_data/ground_truth.csv")
```

```powershell
python scripts\evaluate_cer.py
```

CER 不达标排查顺序：
1. 图片预处理：放大 2 倍、转灰度、提高对比度。
2. 换高精度接口：`BAIDU_OCR_URL=https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic`（按次计费，先小批量试）。
3. 检查标注与图片是否完全一致（漏字、多字都拉高 CER）。

### 6.3 `scripts/evaluate_recall.py`（目标 Recall@10 ≥ 95%）

```python
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clip_service import embed_image, embed_text
from db_milvus import insert_embedding, search_embedding


def main(csv_path: str = "eval_data/ground_truth.csv", top_k: int = 10):
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # 1. 全部图片入库
    id_of = {}
    for asset_id, row in enumerate(rows, start=1):
        img_path = "eval_data/images/" + row["filename"]
        with open(img_path, "rb") as f:
            vec = embed_image(f.read())
        insert_embedding(asset_id, vec)
        id_of[asset_id] = row["filename"]

    # 2. 逐条查询
    hit_count = 0
    for asset_id, row in enumerate(rows, start=1):
        qvec = embed_text(row["text"])
        hits = search_embedding(qvec, top_k=top_k)
        found = any(h["entity"]["asset_id"] == asset_id for h in hits)
        hit_count += int(found)
        print(f"{row['filename']}: {'命中' if found else '未命中'}")

    recall = hit_count / len(rows)
    print(f"\nRecall@{top_k} = {recall:.4f}（目标 ≥ 0.95）")
    print("达标 [PASS]" if recall >= 0.95 else "未达标 [FAIL]")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "eval_data/ground_truth.csv")
```

```powershell
python scripts\evaluate_recall.py
```

未达标排查：是否用了中文 CLIP、查询词与图片差距是否过大、图片是否模糊/倾斜、先跑 `test_milvus.py` 确认向量后端正常。

---

## 步骤 7 收尾：隐私、密钥、成本、上线

1. **隐私（云端不存原图）**：`.env` 里 `KEEP_ORIGINAL=false`，`main.py` 会在 OCR 成功后立即删原图；若要"以图搜图"，改存 512px 缩略图：

   ```python
   # 在 storage.py 里加一个 save_thumbnail(data, max_size=(512, 512))
   from PIL import Image
   import io
   img = Image.open(io.BytesIO(data)).convert("RGB")
   img.thumbnail((512, 512))
   buf = io.BytesIO()
   img.save(buf, format="JPEG", quality=85)
   # 再复用 save_image 保存 buf.getvalue()
   ```

2. **密钥安全**：`.env` 已被 `.gitignore` 排除，`git status` 确认看不到；上线改用环境变量或云密钥管理服务。
3. **成本控制**：OCR 按次计费，开发吃免费额度；CLIP 模型懒加载单例，勿在接口里重复加载。
4. **本地切云只改三个文件内部**：`db_mysql.py`（`DB_TYPE=mysql` + 连接信息）、`db_milvus.py`（`MILVUS_URI` 改集群地址）、`storage.py`（换 MinIO SDK），其他代码不动。

---

## 附录 A：WSL2 严格用 Milvus Lite（可选）

```powershell
wsl --install -d Ubuntu
```

```bash
wsl
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`.env` 里 `VECTOR_BACKEND=milvus_lite`，之后命令用 bash 版本。

---

## 附录 B：完成进度自查表

| 步骤 | 完成标准 | 状态 |
|---|---|---|
| 0 环境 | `venv` 激活，`import fastapi` 不报错 | ☐ |
| 1 骨架 | 目录结构齐全 | ☐ |
| 2 OCR | `python scripts/test_ocr.py 图.jpg` 打印出文字 | ☐ |
| 3 CLIP | `python scripts/test_clip.py 图.jpg` 输出 512 维 | ☐ |
| 4 存储 | 三条验证命令通过（SQLite、向量库、文件存储） | ☐ |
| 5 Pipeline | 上传返回 `asset_id` + `ocr_text` + `vector_dim` | ☐ |
| 6 评估 | CER ≤ 2% 且 Recall@10 ≥ 95% | ☐ |
| 7 收尾 | 隐私开关、密钥、成本都确认过 | ☐ |

---

## 附录 C：常见报错速查

| 现象 | 原因 | 解决 |
|---|---|---|
| `ModuleNotFoundError: No module named 'milvus_lite'` | Milvus Lite 不支持 Windows | `.env` 里 `VECTOR_BACKEND=numpy` |
| `ImportError: python-multipart` | 缺上传解析依赖 | `pip install python-multipart` |
| 上传返回 413/400 | 图片超 10MB 或格式不支持 | 调大 `MAX_FILE_MB` 或换 jpg/png |
| OCR 报 `216200` | token 失效或密钥错误 | 检查 `.env` 密钥 |
| 模型下载卡住/失败 | 网络问题 | `$env:HF_ENDPOINT="https://hf-mirror.com"` |
| 启动报端口被占用 | 8000 已在使用 | `run.py` 里端口改成 8001 |
  | 中文 CLIP 加载报错 | 用了 `CLIPModel`，认不出 `ChineseCLIPModel` 配置 | 改用 `ChineseCLIPModel`/`ChineseCLIPProcessor`（见 3.2） |
