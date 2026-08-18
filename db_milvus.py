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
