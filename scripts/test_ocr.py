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
