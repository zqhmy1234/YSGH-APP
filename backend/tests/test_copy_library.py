"""文案库加载测试（US-21 兜底 · Wave1-B2 · C8 三态矩阵）

覆盖：数据存在且合规 / 文件缺失回退 / 文件损坏回退 / 键缺失按场景回退 /
候选数组形态 / notify 消费点经加载器取文案（集成）。
加载器为纯文件读取（无 DB）→ unit；notify 集成需 DB → integration。
"""
import json
import uuid
from datetime import datetime
from pathlib import Path

import app.services.copy_library as cl
import pytest
from app.services.notify import CARE_TEMPLATES, REVIEW_TZ

SCENES = ("sad_ask", "sad_respond", "angry", "late_night", "day2", "day3")


@pytest.fixture()
def copy_dir(tmp_path, monkeypatch):
    """把 COPY_LIBRARY_DIR 指到临时目录 + 清缓存（teardown 恢复）"""
    d = tmp_path / "copy_library"
    d.mkdir()
    monkeypatch.setattr(cl, "COPY_LIBRARY_DIR", d)
    cl.reload_care_templates()
    yield d
    cl.reload_care_templates()


def _write(d: Path, data, name: str = "care_copy.json") -> None:
    (d / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _valid_copy() -> dict:
    return {s: {"title": f"标题{s}", "body": f"正文{s}"} for s in SCENES}


# ---------------------------------------------------------------------------
# 三态：数据存在 / 缺失 / 损坏
# ---------------------------------------------------------------------------


def test_load_valid_copy(copy_dir):
    """数据文件存在且合规 → 6 场景齐全，get_template 返回文案"""
    _write(copy_dir, _valid_copy())
    loaded = cl.load_care_templates()
    assert set(loaded) == set(SCENES)
    assert cl.get_template("sad_ask") == {"title": "标题sad_ask", "body": "正文sad_ask"}
    assert cl.get_care_templates()["angry"]["body"] == "正文angry"


def test_load_missing_dir_fallback(tmp_path, monkeypatch):
    """文件缺失（目录不存在）→ 回退内置 CARE_TEMPLATES，无异常"""
    monkeypatch.setattr(cl, "COPY_LIBRARY_DIR", tmp_path / "does_not_exist")
    cl.reload_care_templates()
    assert cl.load_care_templates() == {}
    assert cl.get_template("sad_ask") == CARE_TEMPLATES["sad_ask"]
    assert cl.get_template("late_night") == CARE_TEMPLATES["late_night"]


def test_load_corrupt_json_fallback(copy_dir):
    """文件损坏（非法 JSON）→ 回退内置，无异常"""
    (copy_dir / "care_copy.json").write_text("{ not valid json !!!", encoding="utf-8")
    cl.reload_care_templates()
    assert cl.load_care_templates() == {}
    assert cl.get_template("angry") == CARE_TEMPLATES["angry"]
    assert cl.get_care_templates()["sad_ask"] == CARE_TEMPLATES["sad_ask"]


def test_load_wrong_shape_fallback(copy_dir):
    """文件合法 JSON 但非 dict（schema 不符）→ 回退内置，无异常"""
    _write(copy_dir, ["not", "a", "dict"])
    cl.reload_care_templates()
    assert cl.load_care_templates() == {}
    assert cl.get_template("day2") == CARE_TEMPLATES["day2"]


# ---------------------------------------------------------------------------
# 键缺失 / 候选数组 / 场景回退
# ---------------------------------------------------------------------------


def test_load_missing_scene_fallback(copy_dir):
    """数据缺某场景键（如 angry）→ 该场景回退内置，其余用数据"""
    data = _valid_copy()
    del data["angry"]
    _write(copy_dir, data)
    cl.reload_care_templates()
    assert cl.get_template("sad_ask")["body"] == "正文sad_ask"  # 数据命中
    assert cl.get_template("angry") == CARE_TEMPLATES["angry"]  # 缺键回退内置
    assert "angry" in cl.get_care_templates()                   # 全集仍含 6 键


def test_load_candidates_form(copy_dir):
    """C8 候选数组形态：{scene: {candidates: [{title, body}, ...]}} → 取首条"""
    _write(
        copy_dir,
        {
            "sad_ask": {
                "candidates": [
                    {"title": "a", "body": "b"},
                    {"title": "c", "body": "d"},
                ]
            }
        },
    )
    cl.reload_care_templates()
    assert cl.load_care_templates()["sad_ask"][0] == {"title": "a", "body": "b"}
    assert cl.get_template("sad_ask") == {"title": "a", "body": "b"}


# ---------------------------------------------------------------------------
# notify 集成：消费点经加载器（触发逻辑零改动）
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_notify_consumes_copy_library(db_user, copy_dir, monkeypatch):
    """maybe_send_emotion_care 经 copy_library 取文案：数据命中 → 用数据正文"""
    import app.services.notify as notify_mod
    from app.db.models import Content

    class _FakeDaytime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 26, 15, 0, 0, tzinfo=REVIEW_TZ)

    monkeypatch.setattr(notify_mod, "datetime", _FakeDaytime)  # 固定白天防 late_night 分支
    _write(copy_dir, _valid_copy())
    cl.reload_care_templates()

    db, user = db_user
    ts = datetime.now(REVIEW_TZ).replace(hour=12, minute=0, second=0, microsecond=0)
    c = Content(
        id=str(uuid.uuid4()), user_id=user.id, content_type="voice", text="唉",
        taken_at=ts, sensitive_status="正常", status="done", source="app",
        emotion={"emotion": "难过", "confidence": 0.9},
    )
    db.add(c)
    db.commit()
    msg = notify_mod.maybe_send_emotion_care(db, c)
    assert msg is not None
    assert msg.payload["template"] == "sad_ask"   # 触发逻辑不变
    assert msg.body == "正文sad_ask"              # 数据文案（非内置占位）
