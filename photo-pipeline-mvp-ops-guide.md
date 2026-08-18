# 照片管线 MVP —— 六步详细操作指南

> 本指南把你规划的六步路线展开成"照着做就能跑通"的操作手册。默认方案是一个 Python 进程：FastAPI 提供接口，百度 OCR 识别文字，CLIP 生成图片向量，SQLite 存元数据、本地文件夹存图片、本地向量库存向量。每一步都包含：**操作 → 代码 → 验证方法**。
>
> 操作系统以 Windows（PowerShell）为主，同时在必要处给出 WSL/Linux 的 bash 命令。

---

## 总览：最终目录结构

做完所有步骤后，`photo_pipeline/` 目录长这样（`uploads/`、`milvus_data/`、`eval_data/` 会由程序自动创建，不手动建）：

```text
photo_pipeline/
├── main.py               # FastAPI 入口，/upload、/search 接口
├── ocr_service.py        # 百度 OCR：拿 token + 识别文字
├── clip_service.py       # CLIP：图片/文字 → 512 维向量
├── db_mysql.py           # 关系库：默认 SQLite，可切 MySQL
├── db_milvus.py          # 向量库：Milvus Lite / 本地简易后端
├── storage.py            # 图片文件存储（本地文件夹，可换 MinIO）
├── run.py                # 一键启动服务
├── requirements.txt      # 依赖列表
├── .env                  # 密钥配置（已被 .gitignore 排除）
├── .gitignore
├── scripts/              # 测试与评估脚本
│   ├── test_ocr.py
│   ├── test_clip.py
│   ├── test_milvus.py
│   ├── evaluate_cer.py
│   └── evaluate_recall.py
├── uploads/              # 上传的图片（运行时自动生成）
├── milvus_data/          # 向量库文件（运行时自动生成）
└── eval_data/            # 评估用的图片 + 标注 CSV（第 6 步准备）
```

---

## 第 0 步 环境准备（约 20 分钟）

### 0.1 确认 Python 版本

打开 PowerShell，运行：

```powershell
python --version
```

要求 **Python 3.10 ~ 3.12**。如果没有 Python，去 [python.org](https://www.python.org/downloads/) 下载安装，安装时**务必勾选 "Add Python to PATH"**。

### 0.2 创建虚拟环境

虚拟环境把依赖隔离在当前项目里，避免污染系统环境。

PowerShell：

```powershell
cd C:\Users\ZLY20\Documents\Codex\2026-08-18\mvp-1-bash-photo-pipeline-main-2
py -m venv .venv
.venv\Scripts\Activate
```

WSL / Linux：

```bash
cd ~
python3 -m venv .venv
source .venv/bin/activate
```

命令提示符前出现 `(.venv)` 就说明激活成功。

### 0.3 创建 requirements.txt 并安装依赖

新建文件 `requirements.txt`，内容如下：

```text
fastapi>=0.110,<1
uvicorn[standard]>=0.29,<1
python-multipart>=0.0.9
requests>=2.31
python-dotenv>=1.0
pillow>=10.2
numpy>=1.26,<2
torch>=2.2
transformers>=4.40,<5
pymilvus>=2.4,<2.5    # Windows 装不上就删掉这行，代码会自动用本地简易向量后端
rapidfuzz>=3.8        # 评估 CER 时用
pymysql>=1.1          # 只用 SQLite 的话这行可以删掉
```

安装（**先装 CPU 版 PyTorch，体积小、不用配 CUDA**）：

```powershell
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

有 NVIDIA 显卡、想用 GPU 加速的话，第一行改成 `pip install torch` 即可（会下载较大的 CUDA 版本）。

验证安装：

```powershell
python -c "import fastapi, requests, dotenv, PIL, torch, transformers; print('依赖 OK')"
```

### 0.4 创建 .env 和 .gitignore

新建 `.env`（存密钥，绝不提交到 Git）：

```text
# ---------- 百度 OCR ----------
BAIDU_API_KEY=在这里填你的API_Key
BAIDU_SECRET_KEY=在这里填你的Secret_Key
BAIDU_OCR_URL=https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic

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
# auto 会先试 Milvus Lite，失败自动切本地简易后端（Windows 建议直接写 numpy）
VECTOR_BACKEND=auto
MILVUS_URI=milvus_data/milvus_local.db

# ---------- 文件存储 ----------
STORAGE_DIR=uploads
MAX_FILE_MB=10
KEEP_ORIGINAL=true

# ---------- 模型 ----------
# 中文场景推荐 Chinese-CLIP，英文/通用场景用 openai/clip-vit-base-patch32
CLIP_MODEL=OFA-Sys/chinese-clip-vit-base-patch16
```

新建 `.gitignore`：

```text
.venv/
__pycache__/
*.pyc
.env
uploads/
milvus_data/
photo_pipeline.db
eval_data/
test_images/
```

### 0.5 重要说明：Milvus Lite 与 Windows

Milvus Lite 官方目前**只支持 Linux 和 macOS，Windows 本机装不了**。两条路：

1. **Windows 本机直接跑（推荐，零额外环境）**：`db_milvus.py` 里内置一个纯 Python 的简易向量后端（numpy 暴力余弦检索），接口和 Milvus 完全一致，后面上服务器时把 `.env` 改成 Milvus 地址即可，**其他代码一行都不用改**。
2. **严格使用 Milvus Lite**：装 WSL2（见附录 A），在 Ubuntu 里跑。

本指南正文按路线 1 编写，第 4 步会同时给出两种后端代码。

---

## 第 1 步 搭项目骨架（约 10 分钟）

创建项目目录和空文件：

```powershell
New-Item -ItemType Directory -Force photo_pipeline
cd photo_pipeline
New-Item -ItemType Directory -Force scripts test_images
New-Item -ItemType File -Force main.py, ocr_service.py, clip_service.py, db_mysql.py, db_milvus.py, storage.py, requirements.txt, .env, .gitignore, run.py
```

（如果已经在别处建好了 `requirements.txt`、`.env`、`.gitignore`，跳过重复创建即可。）

每个文件一句话职责：

| 文件 | 职责 |
|---|---|
| `main.py` | FastAPI 入口，定义 `/upload`、`/search`、`/health` 接口 |
| `ocr_service.py` | 获取百度 token、调用 OCR、返回识别文字 |
| `clip_service.py` | 加载 CLIP 模型，图片/文字 → 512 维向量 |
| `db_mysql.py` | 元数据入库（默认 SQLite，可切 MySQL） |
| `db_milvus.py` | 向量入库与检索（Milvus Lite / 本地简易后端） |
| `storage.py` | 图片文件保存/删除（本地文件夹，可换 MinIO） |
| `run.py` | 一键启动服务 |
| `scripts/` | 各步骤的测试与评估脚本 |

骨架阶段先不写内容，接下来每步逐个填充。

---

## 第 2 步 跑通百度 OCR（约 30 分钟）

### 2.1 开通服务、拿到密钥

1. 打开[百度智能云控制台](https://console.bce.baidu.com/)并登录。
2. 搜索"**文字识别**"，进入产品页，点击"**创建应用**"（或"立即使用"）。
3. 应用创建后，在应用列表里记下 **API Key** 和 **Secret Key**。
4. 把两个密钥填进 `.env` 的 `BAIDU_API_KEY` 和 `BAIDU_SECRET_KEY`。

> 计费提醒：通用文字识别每天有免费调用额度，超出才按次收费。开发阶段够用，但不要在脚本里写死循环疯狂调用。

### 2.2 编写 ocr_service.py

原理：先用 API Key / Secret Key 换一个 `access_token`（有效期 30 天，程序里做缓存），再用 token 调识别接口。把图片转成 base64 字符串随请求发出。

```python
import base64
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
OCR_URL = os.getenv("BAIDU_OCR_URL", "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic")

_token_cache = {"token": None, "expire_at": 0}


def get_access_token() -> str:
    """获取百度 access_token，带缓存，过期前自动刷新。"""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expire_at"] - 600:
        return _token_cache["token"]

    params = {
        "grant_type": "client_credentials",
        "client_id": os.getenv("BAIDU_API_KEY"),
        "client_secret": os.getenv("BAIDU_SECRET_KEY"),
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
    payload = {"image": base64.b64encode(image_bytes).decode("utf-8")}
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

常见错误码速查：

| 错误码 | 含义 | 处理 |
|---|---|---|
| `216200` | token 无效或过期 | 检查密钥是否正确，程序会自动重新获取 token |
| `17` | 当日免费额度用完 | 等次日恢复，或开通付费 |
| `18` | QPS 超限（每秒调用太多） | 调用之间加 `time.sleep(0.5)` |
| `19` | 总请求量超限 | 检查计费/额度 |

### 2.3 写测试脚本并验证

新建 `scripts/test_ocr.py`：

```python
import sys

from ocr_service import ocr_image

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "test_images/sample.jpg"
    with open(path, "rb") as f:
        text = ocr_image(f.read())
    print("识别结果：")
    print(text)
```

准备一张测试图（手机拍一张含文字的纸、电脑屏幕截图都行），放到 `test_images/`，然后运行：

```powershell
python scripts/test_ocr.py test_images\sample.jpg
```

**验证点**：能打印出图片中的文字，就说明第 2 步完成。

---

## 第 3 步 跑通 CLIP 向量化（约 30–60 分钟，含模型下载）

### 3.1 模型选择

- `openai/clip-vit-base-patch32`：英文图文模型，输出 512 维向量。
- `OFA-Sys/chinese-clip-vit-base-patch16`：中文图文模型（Chinese-CLIP），也输出 512 维向量，**中文场景推荐直接用它**。

第一次加载模型会自动从 HuggingFace 下载约 600MB，需要联网。

### 3.2 编写 clip_service.py

```python
import io
import os

import torch
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

MODEL_NAME = os.getenv("CLIP_MODEL", "openai/clip-vit-base-patch32")

_model = None
_processor = None


def _get_model():
    """懒加载：只有第一次调用时才真正加载模型（约 600MB，只加载一次）。"""
    global _model, _processor
    if _model is None:
        from transformers import CLIPModel, CLIPProcessor

        _model = CLIPModel.from_pretrained(MODEL_NAME, trust_remote_code=True)
        _processor = CLIPProcessor.from_pretrained(MODEL_NAME, trust_remote_code=True)
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
        feats = _normalize(model.get_image_features(**inputs))
    return feats[0].tolist()


def embed_text(text: str) -> list:
    """文字 → 归一化后的 512 维向量（用于以文搜图）。"""
    model, processor = _get_model()
    inputs = processor(text=text, return_tensors="pt")
    with torch.no_grad():
        feats = _normalize(model.get_text_features(**inputs))
    return feats[0].tolist()
```

两个说明：

- 向量先归一化，这样"余弦相似度"和"内积"等价，Milvus 里选 `COSINE` 或 `IP` 都行。
- `trust_remote_code=True` 是中文 CLIP 仓库要求的；用 OpenAI 英文模型时这个参数无副作用。如果新版 transformers 报"不需要 remote code"，去掉该参数即可。

### 3.3 写测试脚本并验证

新建 `scripts/test_clip.py`：

```python
import sys

from clip_service import embed_image

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "test_images/sample.jpg"
    with open(path, "rb") as f:
        vec = embed_image(f.read())
    print("向量维度:", len(vec))
    print("前 5 个数值:", vec[:5])
    assert len(vec) == 512, "向量维度不是 512，请检查模型"
    print("维度验证通过 ✓")
```

运行：

```powershell
python scripts/test_clip.py test_images\sample.jpg
```

**验证点**：打印出"向量维度: 512"，且数值都在 -1 到 1 之间。

### 3.4 模型下载慢/失败怎么办

国内网络常见问题，两种解法：

1. 用国内镜像站。PowerShell 里先设置环境变量再运行：
   ```powershell
   $env:HF_ENDPOINT = "https://hf-mirror.com"
   ```
   WSL 里用 `export HF_ENDPOINT=https://hf-mirror.com`。
2. 手动下载模型文件到缓存目录。模型默认缓存位置：
   ```text
   C:\Users\你的用户名\.cache\huggingface\hub\models--OFA-Sys--chinese-clip-vit-base-patch16
   ```
   在浏览器打开模型页面逐个下载文件放进去（或让下载工具帮忙），再运行脚本就会直接用缓存。

---

## 第 4 步 接入存储（约 60 分钟）

### 4.1 关系库：db_mysql.py（SQLite 默认，MySQL 可选）

设计思路：文件名沿用你规划里的 `db_mysql.py`，但内部用 `.env` 的 `DB_TYPE` 切换。本地开发零配置用 SQLite（Python 自带），上云时改成 `DB_TYPE=mysql` 即可。

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

快速验证（PowerShell）：

```powershell
python -c "import db_mysql; db_mysql.init_db(); aid=db_mysql.save_asset('user1','uploads/test.jpg','你好世界'); print('asset_id =', aid); print(db_mysql.get_asset(aid))"
```

**验证点**：输出 `asset_id = 1` 和包含 `ocr_text` 的记录。

### 4.2 向量库：db_milvus.py（Milvus Lite / 本地简易后端）

设计思路：统一提供 `insert_embedding(asset_id, vec)` 和 `search_embedding(vec, top_k)` 两个函数。后端由 `.env` 的 `VECTOR_BACKEND` 决定：

- `milvus_lite`：用 `MilvusClient` 存到本地 `.db` 文件（Linux/macOS/WSL）。
- `numpy`：纯 Python 本地向量索引（Windows 本机用），接口返回结构跟 Milvus 一致。
- `auto`：先试 Milvus，失败自动切 numpy。

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

新建 `scripts/test_milvus.py` 验证：

```python
import random

from db_milvus import insert_embedding, search_embedding

if __name__ == "__main__":
    vec = [random.random() for _ in range(512)]
    insert_embedding(1, vec)
    hits = search_embedding(vec, top_k=3)
    print("检索结果:", hits)
    assert hits and hits[0]["entity"]["asset_id"] == 1, "应能搜回刚插入的向量"
    print("向量库验证通过 ✓")
```

运行：

```powershell
python scripts\test_milvus.py
```

**验证点**：第一条命中结果就是刚插入的 `asset_id = 1`，且距离接近 1。

> 以后上服务器/云环境，只需把 `.env` 里的 `MILVUS_URI` 改成 Milvus 集群地址（如 `http://192.168.1.10:19530`），其余代码不动。

### 4.3 文件存储：storage.py（本地文件夹，可换 MinIO）

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

几点说明：

- 扩展名白名单 + 大小上限，避免用户乱传文件。
- 文件名用 UUID 重命名，避免重名和中文名问题。
- 换 MinIO 时只需要改这个文件：把 `save_image`/`delete_image` 内部实现换成 minio SDK（`pip install minio`）的上传/删除，调用方代码不用动。

快速验证：

```powershell
python -c "import storage; p=storage.save_image(b'fake-image-data','test.png'); print(p); storage.delete_image(p); print('OK')"
```

**验证点**：打印出 `uploads/2026/08/18/一串字符.png`，随后文件被删除。

---

## 第 5 步 串成完整 Pipeline（约 60 分钟）

### 5.1 编写 main.py

`/upload` 的流程：接收图片 → 存文件 → OCR → CLIP 向量化 → 元数据入库 → 向量入库 → 返回结果。另加 `/search` 演示"以文搜图"。

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

    # 2. OCR
    try:
        text = ocr_image(data)
    except Exception as e:
        storage.delete_image(rel_path)
        raise HTTPException(status_code=502, detail=f"OCR 失败: {e}")

    # 3. CLIP 向量化
    vector = embed_image(data)

    # 4. 元数据 + 向量入库
    asset_id = db_mysql.save_asset(user_id, rel_path, text)
    db_milvus.insert_embedding(asset_id, vector)

    # 5. 隐私要求：云端不存原图时，识别完立刻删除
    if os.getenv("KEEP_ORIGINAL", "true").lower() != "true":
        storage.delete_image(rel_path)
        rel_path = ""  # 返回空路径，表示原图已删除

    return {
        "asset_id": asset_id,
        "file_path": rel_path,
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

### 5.2 编写 run.py（一键启动）

```python
import uvicorn

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
```

启动服务：

```powershell
python run.py
```

看到 `Uvicorn running on http://127.0.0.1:8000` 即启动成功。

### 5.3 用命令行测试

PowerShell 里注意用 `curl.exe`（`curl` 在 PowerShell 里是别名，行为不一样）：

```powershell
curl.exe -X POST -F "file=@test_images\sample.jpg" http://127.0.0.1:8000/upload
```

WSL / Linux：

```bash
curl -X POST -F "file=@test_images/sample.jpg" http://127.0.0.1:8000/upload
```

预期返回类似：

```json
{
  "asset_id": 1,
  "file_path": "uploads/2026/08/18/1a2b3c4d....jpg",
  "ocr_text": "识别出的文字……",
  "vector_dim": 512
}
```

再测搜索接口：

```powershell
curl.exe -X POST "http://127.0.0.1:8000/search?query=你好&top_k=3"
```

### 5.4 用 Apifox 测试

1. 打开 Apifox，新建一个项目（或用已有的）。
2. 新建请求：
   - 方法：`POST`
   - URL：`http://127.0.0.1:8000/upload`
   - Body 选择 **form-data**，新增字段 `file`，类型选 **File**，从本地选择一张测试图片
3. 点"发送"，看返回 JSON 里是否有 `asset_id`、`ocr_text` 和 `vector_dim: 512`。
4. 再建一个请求测试 `/search?query=任意文字`。

**验证点**：上传返回结果里文字识别正确、向量维度 512，搜索能搜到刚上传的图。

### 5.5 常见报错速查

| 现象 | 原因 | 解决 |
|---|---|---|
| `ModuleNotFoundError: No module named 'milvus_lite'` | Milvus Lite 不支持 Windows | `.env` 里 `VECTOR_BACKEND=numpy` |
| `ImportError: python-multipart` | 缺上传解析依赖 | `pip install python-multipart` |
| 上传返回 413/400 | 图片超过 10MB 或格式不支持 | 调大 `MAX_FILE_MB` 或换 jpg/png |
| OCR 报 `216200` | token 失效或密钥错误 | 检查 `.env` 密钥 |
| 模型下载卡住/失败 | 网络问题 | 设 `HF_ENDPOINT=https://hf-mirror.com`（见 3.4） |
| 启动报端口被占用 | 8000 已在使用 | `python run.py` 里把端口改成 8001 |
| 中文 CLIP 加载报错 | transformers 版本太旧 | `pip install -U transformers` |

---

## 第 6 步 评估（约半天）

### 6.1 准备测试集

建如下结构：

```text
eval_data/
├── images/            # 20–50 张 jpg/png，命名如 img_001.jpg
└── ground_truth.csv   # 人工标注
```

`ground_truth.csv` 格式（第一行是表头，UTF-8 编码）：

```csv
filename,text
img_001.jpg,你好世界这是测试文字
img_002.jpg,发票号码 12345678
img_003.jpg,第二行文字
```

标注规则：

- 图片里有什么文字就抄什么，尽量覆盖真实业务场景（发票、名片、截图、菜单等）。
- OCR 是按行返回的，标注时也按从上到下、从左到右的顺序写；评估脚本会去掉换行和空格再比对，所以标点/空格不影响太大。
- 手写体、艺术字很难达到 2% 的 CER，评估结果要结合图片类型解读。

### 6.2 计算 CER（字符错误率），目标 ≤ 2%

CER = 编辑距离 ÷ 标注文本字符数。0 表示完全识别正确，0.05 表示每 100 个字错 5 个。

新建 `scripts/evaluate_cer.py`：

```python
import csv
import sys

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
    for row in rows:
        img_path = "eval_data/images/" + row["filename"]
        with open(img_path, "rb") as f:
            pred = ocr_image(f.read())
        e = cer(pred, row["text"])
        total_err += e * len(normalize(row["text"]))
        total_chars += len(normalize(row["text"]))
        print(f"{row['filename']}: CER = {e:.4f}")

    avg_cer = total_err / total_chars if total_chars else 0.0
    print(f"\n平均 CER = {avg_cer:.4f}（目标 ≤ 0.02）")
    print("达标 ✓" if avg_cer <= 0.02 else "未达标 ✗")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "eval_data/ground_truth.csv")
```

运行：

```powershell
python scripts\evaluate_cer.py
```

如果 CER 不达标，按顺序排查：

1. 图片预处理：放大 2 倍、转灰度、提高对比度后再识别。
2. 换更准的接口：`BAIDU_OCR_URL` 改成 `https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic`（精度更高，按次计费，先小批量试）。
3. 检查标注是否与图片完全一致（漏字、多字都会拉高 CER）。

### 6.3 计算向量检索 Recall@10，目标 ≥ 95%

思路：把测试图片全部入库 → 对每张图用它的标注文字作为查询 → 检索 top 10 → 统计正确图片是否在结果里。

新建 `scripts/evaluate_recall.py`：

```python
import csv
import sys

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
    print("达标 ✓" if recall >= 0.95 else "未达标 ✗")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "eval_data/ground_truth.csv")
```

运行：

```powershell
python scripts\evaluate_recall.py
```

说明与排查：

- 这里用 OCR 标注文字当查询词，测的是"文字内容搜图"。如果将来做"描述性搜索"（比如搜"夕阳下的海边"），需要单独写查询描述并重新标注，脚本结构不变。
- 未达标时依次检查：是否用了中文 CLIP（英文模型搜中文图效果差）、查询词是否与图片内容差距太大、图片是否模糊/倾斜、向量库后端是否正确（`test_milvus.py` 先跑一遍）。

---

## 第 7 步 收尾：隐私、密钥、成本、上线

1. **隐私合规（文档里"云端不存原图"）**：把 `.env` 的 `KEEP_ORIGINAL` 改成 `false`。`main.py` 里 OCR 成功后立即删除原图，只保留文字和向量；如果还需要"以图搜图"，建议存压缩后的缩略图而不是原图。缩略图可以这样生成：
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
2. **密钥安全**：`.env` 已被 `.gitignore` 排除，确认 `git status` 里看不到它；上线时改用环境变量或云厂商的密钥管理服务。
3. **成本控制**：OCR 按次计费，开发阶段吃免费额度即可；CLIP 模型只在进程内加载一次（代码已是懒加载单例），不要在接口里重复加载。
4. **从本地切云，只改三个文件内部**：
   - `db_mysql.py`：`.env` 改 `DB_TYPE=mysql` + MySQL 连接信息（或 RDS）。
   - `db_milvus.py`：`MILVUS_URI` 改成 Milvus 集群地址。
   - `storage.py`：内部换成 MinIO SDK 上传/下载。
   - 其他代码不用动——这就是第 0 步设计统一接口的意义。

---

## 附录 A：WSL2 里严格用 Milvus Lite（可选）

如果你希望严格按原计划用 Milvus Lite：

1. PowerShell 安装 WSL2（装完按提示重启，设置 Linux 用户名密码）：
   ```powershell
   wsl --install -d Ubuntu
   ```
2. 进入 WSL，创建虚拟环境并安装依赖：
   ```bash
   wsl
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. `.env` 里设置 `VECTOR_BACKEND=milvus_lite`。
4. 之后的命令全部用指南里的 bash 版本（`curl`、`source .venv/bin/activate` 等）。

## 附录 B：完成进度自查表

| 步骤 | 完成标准 | 状态 |
|---|---|---|
| 0 环境 | 虚拟环境激活，`python -c "import fastapi"` 不报错 | ☐ |
| 1 骨架 | 目录结构齐全 | ☐ |
| 2 OCR | `python scripts/test_ocr.py 图.jpg` 打印出文字 | ☐ |
| 3 CLIP | `python scripts/test_clip.py 图.jpg` 输出 512 维 | ☐ |
| 4 存储 | 三条验证命令都通过（SQLite、向量库、文件存储） | ☐ |
| 5 Pipeline | Apifox 上传返回 `asset_id` + `ocr_text` + `vector_dim` | ☐ |
| 6 评估 | CER ≤ 2% 且 Recall@10 ≥ 95% | ☐ |
| 7 收尾 | 隐私开关、密钥、成本都确认过 | ☐ |
