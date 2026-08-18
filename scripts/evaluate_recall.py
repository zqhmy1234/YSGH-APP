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
