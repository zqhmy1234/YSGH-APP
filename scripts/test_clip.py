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
