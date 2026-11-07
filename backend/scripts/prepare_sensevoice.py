"""部署前下载并校验 SenseVoice 模型资产。

用法：python scripts/prepare_sensevoice.py --target models/SenseVoiceSmall-onnx
部署时再把 SENSEVOICE_MODEL_DIR 指向同一目录。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.external.asr import prepare_sensevoice_assets  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="预置 SenseVoiceSmall-ONNX 模型")
    parser.add_argument(
        "--target",
        type=Path,
        default=BACKEND_DIR / "models" / "SenseVoiceSmall-onnx",
        help="模型落盘目录",
    )
    args = parser.parse_args()
    model_dir = prepare_sensevoice_assets(args.target)
    print(f"SenseVoice assets ready: {model_dir}")


if __name__ == "__main__":
    main()
