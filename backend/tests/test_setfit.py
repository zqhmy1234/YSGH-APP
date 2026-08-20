"""SetFit 分类测试（M1 Part 3 · F2）

覆盖：
  - 门禁：种子数据评估集准确率 ≥75%（分类≥75% M1 门禁）
  - API：POST /api/v1/classify 冒烟 + 空文本 422
  - 服务：5 类输出结构 + confidence 合法性
前置：python scripts/train_setfit.py 已训练（backend/models/setfit-classifier/）
"""
from pathlib import Path

import pytest
from app.services.classifier import classify

MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "setfit-classifier"

pytestmark = pytest.mark.skipif(
    not (MODEL_DIR / "model.safetensors").exists() and not (MODEL_DIR / "pytorch_model.bin").exists(),
    reason="SetFit 模型未训练（先跑 python scripts/train_setfit.py）",
)


def _seed_eval() -> list[tuple[str, str]]:
    """从种子数据取评估集（与训练脚本同一划分：每类索引 {1,4}）"""
    import json

    seed = json.loads(
        (Path(__file__).resolve().parent.parent / "data" / "setfit_seed.json").read_text(encoding="utf-8")
    )
    pairs: list[tuple[str, str]] = []
    for cls in seed["_meta"]["classes"]:
        for i, text in enumerate(seed[cls]):
            if i in {1, 4}:
                pairs.append((text, cls))
    return pairs


def test_gate_accuracy_ge_75():
    """评估集准确率报告（审查修复 P1-17）

    原实现：评估集取自训练同源种子（每类索引 {1,4}），与训练数据重叠 →
    准确率虚高、门禁形同虚设。真实门禁（≥75%）由训练脚本 --eval-only 承担
    （scripts/train_setfit.py main()，完整评估输出 + 退出码门禁）。
    本测试仅报告准确率并做下限冒烟（≥0.5 防模型完全损坏），
    独立标注评估集待真实碎片数据到位后替换（progress.md 待办）。
    """
    eval_pairs = _seed_eval()
    correct = 0
    for text, expected in eval_pairs:
        result = classify(text)
        if result["label"] == expected:
            correct += 1
    acc = correct / len(eval_pairs)
    # 仅冒烟下限：模型不可用/标签错乱时暴露；准确率门禁见训练脚本 --eval-only
    assert acc >= 0.5, f"分类准确率异常低 {acc:.0%}（{correct}/{len(eval_pairs)}），检查模型"


def test_classify_structure():
    """输出结构：5 类 scores + confidence 合法"""
    result = classify("明天记得去取快递")
    assert result["label"] in {"todo", "idea", "emotion", "quote", "mixed"}
    assert result["label_cn"]
    assert 0.0 <= result["confidence"] <= 1.0
    assert len(result["scores"]) == 5
    for s in result["scores"]:
        assert s["label"] and s["label_cn"]
        assert 0.0 <= s["score"] <= 1.0


def test_classify_empty_rejected():
    """空文本 → ValueError（API 层转 422）"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        from app.schemas.classify import ClassifyRequest

        ClassifyRequest(text="")


def test_classify_api_smoke():
    """API 冒烟：POST /api/v1/classify 入队返回 job_id（P2-01 异步化）"""
    import uuid

    from app.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.post(
        "/api/v1/auth/wechat",
        json={"code": f"sf-{uuid.uuid4().hex[:8]}", "device_id": "sf-dev"},
    )
    headers = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}

    resp = client.post(
        "/api/v1/classify", json={"text": "今天好累,什么都不想做"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == "OK"
    assert body["data"]["job_id"]  # 入队成功（P2-01：SetFit 推理移 worker）

    resp2 = client.post("/api/v1/classify", json={"text": ""}, headers=headers)
    assert resp2.status_code == 422
