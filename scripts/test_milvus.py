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
