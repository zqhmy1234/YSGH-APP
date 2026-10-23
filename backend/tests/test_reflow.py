"""共性纠错微调流水线测试（B5-c-4 · 脚本逻辑）"""


import pytest

from scripts.reflow_global import _backup_model, _promote_if_gate, build_dataset


def test_classifier_wordlist_is_seven():
    """2026-10-23 契约锁：分类词表必须是 7 类，且中英一一对应。"""
    from app.services.classifier import (
        DEFAULT_CLASSES,
        DEFAULT_CLASSES_CN,
        LABEL_CN_MAP,
        VALID_CLASSES,
    )

    assert DEFAULT_CLASSES == ["todo", "idea", "emotion", "quote", "mixed", "question", "fact"]
    assert len(DEFAULT_CLASSES_CN) == len(DEFAULT_CLASSES) == 7
    assert VALID_CLASSES == set(DEFAULT_CLASSES)
    assert LABEL_CN_MAP["question"] == "问询"
    assert LABEL_CN_MAP["fact"] == "事实"


def test_build_dataset_groups_by_label():
    """候选 → 按 new_label 分组，空文本跳过"""
    candidates = [
        {"text": "记得交房租", "new_label": "todo"},
        {"text": "", "new_label": "todo"},          # 空文本跳过
        {"text": "想做个app", "new_label": "idea"},
        {"text": "人生如逆旅", "new_label": "quote"},
        {"text": "今天好累", "new_label": "emotion"},
        {"text": "随便聊聊", "new_label": "mixed"},
        {"text": "我今天记了什么？", "new_label": "question"},
        {"text": "今天生病了，去医院挂了水", "new_label": "fact"},
        {"text": "非法标签", "new_label": "unknown"},  # 非法标签丢弃
    ]
    ds = build_dataset(candidates)
    assert ds["todo"] == ["记得交房租"]
    assert ds["idea"] == ["想做个app"]
    assert ds["quote"] == ["人生如逆旅"]
    assert ds["emotion"] == ["今天好累"]
    assert ds["mixed"] == ["随便聊聊"]
    assert ds["question"] == ["我今天记了什么？"]
    assert ds["fact"] == ["今天生病了，去医院挂了水"]
    assert "unknown" not in ds
    total = sum(len(v) for v in ds.values())
    assert total == 7


def test_backup_model_backs_up_and_prunes(tmp_path):
    """训练前备份（审查 MAJOR）：生产模型完整备份，仅保留最近 keep 份"""
    model_dir = tmp_path / "setfit-classifier"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_text("old-model", encoding="utf-8")
    backup_root = tmp_path / "backups"

    backup = _backup_model(model_dir, backup_root, keep=1)
    assert backup is not None and backup.exists()
    assert (backup / "model.safetensors").read_text(encoding="utf-8") == "old-model"
    assert model_dir.exists(), "备份不删除原模型"

    # 手工再放两份更旧的备份 → keep=1 只留最新
    (backup_root / "setfit-classifier-20260101000000").mkdir()
    (backup_root / "setfit-classifier-20260102000000").mkdir()
    _backup_model(model_dir, backup_root, keep=1)
    remain = sorted(p.name for p in backup_root.glob("setfit-classifier-*"))
    assert len(remain) == 1, f"keep=1 应只剩最新备份，实际 {remain}"


def test_backup_model_skips_when_no_model(tmp_path):
    """无生产模型（首次训练）→ 跳过备份"""
    backup_root = tmp_path / "backups"
    assert _backup_model(tmp_path / "not-exists", backup_root) is None
    assert not backup_root.exists()


def test_promote_if_gate_replaces_model(tmp_path):
    """门禁达标（≥75%）→ staging 换入生产目录"""
    model_dir = tmp_path / "setfit-classifier"
    model_dir.mkdir()
    (model_dir / "old.txt").write_text("old", encoding="utf-8")
    staging = tmp_path / "setfit-classifier-staging"
    staging.mkdir()
    (staging / "new.txt").write_text("new", encoding="utf-8")

    _promote_if_gate(staging, model_dir, acc=0.8)
    assert (model_dir / "new.txt").read_text(encoding="utf-8") == "new"
    assert not (model_dir / "old.txt").exists()
    assert not staging.exists()


def test_promote_if_gate_aborts_when_below_gate(tmp_path):
    """门禁不达标 → 抛错、staging 清理、生产模型不动（审查 MAJOR 回滚兜底）"""
    model_dir = tmp_path / "setfit-classifier"
    model_dir.mkdir()
    (model_dir / "old.txt").write_text("old", encoding="utf-8")
    staging = tmp_path / "setfit-classifier-staging"
    staging.mkdir()
    (staging / "new.txt").write_text("new", encoding="utf-8")

    with pytest.raises(RuntimeError):
        _promote_if_gate(staging, model_dir, acc=0.5)
    assert (model_dir / "old.txt").read_text(encoding="utf-8") == "old"
    assert not staging.exists(), "不达标 staging 必须清理"
