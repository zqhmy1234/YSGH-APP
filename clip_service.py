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
