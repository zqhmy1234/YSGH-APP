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
