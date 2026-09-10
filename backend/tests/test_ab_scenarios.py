"""AB 场景级端到端联调测试（2026-09-08 · 五连环用户故事）

峰宝原话：「构造连环的用户故事场景、测试数据，设点断言，像 apifox 那样。
否则只看 openapi 是没用的。」——本文件以真实 HTTP（TestClient 进程内 ASGI）
+ 出参契约 + SessionLocal 直查 DB 的三级断言，串联五个用户故事：

- S1 语音记忆全旅程（TestS1VoiceJourney）
    造用户（11 位手机号）→ POST /contents（voice+remark+duration_ms+taken_at）
    → 进程内 process_content（mock ASR 转写 + SetFit 分类替身）+ tag_content
    （monkeypatch 打标）→ DB 回查 tags_json/duration/taken_at → 详情
    （GET /contents?content_id=，无 GET /{id} 详情路由——契约偏差①②见下）
    → PATCH remark（含越权字段 422）→ favorite → favorites 列表 → 软删
    → 列表消失 → trash days_left=30 → restore → waveform 已知峰值
    → /users/me 掩码（响应 JSON 不得含完整手机号明文）→ /stats/daily-summary
    total_bytes
- S2 时间胶囊全生命周期（TestS2CapsuleLifecycle）
    封存 → 列表 sealed+content 摘要 → 未到期 409 CAPSULE_003（remaining_days）
    → 直改库到期 → 节流归零 → GET /capsules 惰性扫描置 due → capsule_due 消息
    （content_id 列+payload 双断言）→ 二次扫描幂等 → 到期开启（content 回传）
    → 已开启撤销 409 CAPSULE_004 → 第二条封存 → 撤销成功
- S3 聚合与时间轴（TestS3Aggregation）
    3 条语音同 content_class=mixed（7 天窗 ≥3 次跨 2 天）→ aggregate_user 建
    L3「标签 · mixed」→ 增量 +2 条并入（R9-C 回归：同标签新成员必须挂接不丢）
    → timeline content_count/voice 单条直出 → confirm（带 title）→
    新成员不再并入（confirmed 双层保护）
- S4 越权与边界矩阵（TestS4AuthzMatrix）
    双用户：B 对 A 的内容 favorite/PATCH/waveform/软删四路全 404 CONTENT_010
    （IDOR 不区分不存在/越权）；buckets=0/129 → 422；text 内容 waveform →
    404 MEDIA_003；非 11 位 phone → 掩码 null
- S5 回响与消息链（TestS5EchoChain）
    DB 直插去年今日内容 → get_today_echo 命中即建发 → EchoHistory 与 echo
    消息各恰 1 条（payload 与 content_id 列双断言）→ GET /messages 出参契约

契约偏差（计划 vs 实际代码，以实际为准）：
① POST /contents 的 voice 路径不回填 size_bytes（仅分片上传 complete 路径回填）
   ——S1 在 DB 层补 size_bytes 模拟上传路径后再断言 total_bytes；
② mock 模式 generate_tags 抛 AiTaggingError(LLM_NOT_CONFIGURED)、tags_json 不回填
   ——monkeypatch generate_tags 后进程内直调 tag_content 验证回填契约；
③ 无 GET /contents/{id} 详情路由，详情走 GET /contents?content_id=；
④ timeline 无 voice 计数字段，实际是单条 voice VoiceInfo 对象 + content_count；
⑤ timeline voice 对象不下发 duration（恒 None，客户端播放时由
   InnerAudioContext.duration 补齐——_batch_event_voices docstring 明示）。

实证发现（环境）：本测试库 event_edit_log.id 缺 bigserial 序列（schema.sql 定义
bigserial，实际建表漂移无 default/无序列）→ merge/split/confirm 一律 IntegrityError。
test_event_ops.py 7 个存量用例同因失败（非本文件引入）。已按 schema.sql 幂等补建
序列 event_edit_log_id_seq + SET DEFAULT nextval 对齐，存量用例随之恢复。

先例：test_ba2_capsule（胶囊契约/节流复位）、test_bb3_waveform（WAV 手写/峰值）、
test_data_chains_12（echo payload 守卫/_last_year 闰年回退）、test_l3_voice_chain
（整链 journey fixture 编排）。
前置：本地 PostgreSQL yishu + Redis（对齐 test_ba1/test_ba2）；AI 全 mock +
storage 走 fake（backend/conftest.py _force_test_env autouse 强制）。
"""
import json
import struct
import uuid
from datetime import datetime, time, timedelta, timezone

import app.api.contents as contents_api
import app.core.queue as queue_mod
import app.services.ai_tagging as ai_tagging_mod
import app.services.echo as echo_svc
import app.services.pipeline as pipeline_mod
import app.workers.capsule_scan as capsule_scan_mod
import pytest
from app.db.models import Capsule, Content, EchoHistory, Event, EventItem, Message
from app.db.session import SessionLocal
from app.services.events import aggregate_user
from app.services.external.storage import get_storage_backend
from fastapi.testclient import TestClient
from sqlalchemy import select, text

# ---------------------------------------------------------------------------
# 公共基建
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """DB 直查通道（三级断言的第三级；长会话，每测试独立）"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _wav_bytes(samples: list[int]) -> bytes:
    """手写构造 PCM16 WAV（bb3 同款：RIFF + fmt + data，全部代码内字节）"""
    data = struct.pack(f"<{len(samples)}h", *samples)
    fmt = struct.pack("<HHIIHH", 1, 1, 16000, 16000 * 2, 2, 16)
    out = b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE"
    out += b"fmt " + struct.pack("<I", len(fmt)) + fmt
    out += b"data" + struct.pack("<I", len(data)) + data
    return out


def _seed_wav_storage(user_id: str, wav: bytes) -> str:
    """WAV 落 fake storage（内存单例）→ 返回合法 cos_key（voice/{uid}/ 前缀）"""
    key = f"voice/{user_id}/ab_{uuid.uuid4().hex[:8]}.wav"
    get_storage_backend().put_object(key, wav)
    return key


def _update_user_phone(db, user_id: str, phone: str | None) -> None:
    """直改用户手机号（掩码/边界用例的数据准备）。
    先清同号孤儿：前次运行异常中断 teardown 未清的残留行会触发 users_phone_key
    唯一约束冲突（实测：Redis 容器睡眠炸掉全量跑 → S4 中途崩 → 孤儿 10086 留库）。
    置 NULL 而非删行，避开关联数据外键。"""
    if phone:
        db.execute(
            text("UPDATE users SET phone = NULL WHERE phone = :p AND id != :uid"),
            {"p": phone, "uid": str(user_id)},
        )
        db.commit()
    db.execute(
        text("UPDATE users SET phone = :p WHERE id = :uid"),
        {"p": phone, "uid": str(user_id)},
    )
    db.commit()


def _set_content_class(db, content_id: str, cls: str) -> None:
    """直改 content_class（模拟 SetFit 分类结果；聚合按类标签聚 L3 流）"""
    c = db.get(Content, content_id)
    assert c is not None, f"内容不存在: {content_id}"
    c.content_class = cls
    c.class_source = "setfit"
    c.model_version = "setfit-v1"
    db.commit()


def _backfill_size_bytes(db, content_id: str, size: int) -> None:
    """模拟分片上传 complete 路径的 size_bytes 回填（POST /contents voice 路径不回填，
    契约偏差①——用量统计断言前补齐）"""
    c = db.get(Content, content_id)
    assert c is not None, f"内容不存在: {content_id}"
    c.size_bytes = size
    db.commit()


def _wipe_user(db, user_id: str, *, capsules_too: bool = False) -> None:
    """teardown：删 capsules（conftest 统一清理链不含）→ 30+ 表统一清理 → 删用户行"""
    from tests.conftest import cleanup_user_data

    uid = str(user_id)
    if capsules_too:
        db.execute(
            text("DELETE FROM capsules WHERE user_id = :uid"),
            {"uid": uid},
        )
        db.commit()
    cleanup_user_data(db, uid)
    db.execute(
        text("DELETE FROM users WHERE id = :uid"), {"uid": uid}
    )
    db.commit()


def _mk_text(client: TestClient, headers: dict, text_body: str) -> str:
    """建文本内容 → content_id（S2 胶囊/S4 越权矩阵用）"""
    r = client.post(
        "/api/v1/contents",
        json={"content_type": "text", "text": text_body, "source": "app"},
        headers=headers,
    )
    assert r.status_code == 200, f"建文本内容失败: {r.text}"
    return r.json()["data"]["id"]


def _mk_voice(client: TestClient, headers: dict, user_id: str, taken_at: datetime) -> str:
    """建语音内容（WAV 落 storage + taken_at 上送）→ content_id（S3 聚合数据用）"""
    key = _seed_wav_storage(user_id, _wav_bytes([800] * 32))
    r = client.post(
        "/api/v1/contents",
        json={
            "content_type": "voice",
            "cos_key": key,
            "source": "app",
            "taken_at": taken_at.isoformat(),
            "extra": {"duration_ms": 30000},
        },
        headers=headers,
    )
    assert r.status_code == 200, f"建语音内容失败: {r.text}"
    return r.json()["data"]["id"]


def _last_year_taken_at() -> datetime:
    """去年今日 12:00（本地时区；闰年 2/29 → 2/28 回退，echo 域先例同款）"""
    today = echo_svc._local_now().date()
    try:
        last_year = today.replace(year=today.year - 1)
    except ValueError:
        last_year = today.replace(year=today.year - 1, day=28)
    return datetime.combine(last_year, time(12, 0), tzinfo=echo_svc._local_now().tzinfo)


# ---------------------------------------------------------------------------
# S1 语音记忆全旅程
# ---------------------------------------------------------------------------

_VOICE_TAGS = ["家庭", "回忆", "声音"]
_MOCK_ASR_TEXT = "这是一段本地模拟转写文本。"  # asr/backends.py mock 通道固定文本
_S1_TAKEN_AT = "2026-09-07T10:00:00+08:00"  # 客户端拍摄时间（create 即存即出）
_S1_SAMPLES = [12800] * 16 + [-16384] * 16 + [0] * 96  # 128 样本 → 8 桶已知峰值


class TestS1VoiceJourney:
    """S1 场景：录一段语音 → AI 转写打标 → 整理收藏 → 误删找回 → 波形/统计"""

    @pytest.fixture()
    def journey(self, client, auth_headers, db, monkeypatch):
        """整链跑一次（function 作用域：每环节测试重跑整链）；各环节产物存快照。

        管线隔离：RQ 入队（contents 模块 + core/queue 两处）与 Qdrant 索引 no-op；
        AI 替身：SetFit 分类固定 label=emotion、LLM 打标固定三标签
        （mock 环境真 LLM 未配置，见契约偏差②）。
        """
        monkeypatch.setattr(contents_api, "enqueue_unique", lambda *a, **k: None)
        monkeypatch.setattr(queue_mod, "enqueue_unique", lambda *a, **k: None)
        monkeypatch.setattr(pipeline_mod, "_index_after_commit", lambda *a, **k: None)
        monkeypatch.setattr(
            pipeline_mod, "_get_classifier", lambda: (lambda _text: {"label": "emotion"})
        )
        monkeypatch.setattr(ai_tagging_mod, "generate_tags", lambda _content: list(_VOICE_TAGS))

        user_id, headers = auth_headers("s1")
        phone = "138" + uuid.uuid4().hex[:8]  # 11 位唯一手机号
        _update_user_phone(db, user_id, phone)

        wav = _wav_bytes(_S1_SAMPLES)
        cos_key = _seed_wav_storage(user_id, wav)

        # ── 环节① 上传：POST /contents（voice 带 remark/duration_ms/taken_at）──
        r = client.post(
            "/api/v1/contents",
            json={
                "content_type": "voice",
                "cos_key": cos_key,
                "source": "app",
                "remark": "外婆的叮嘱",
                "taken_at": _S1_TAKEN_AT,
                "extra": {"duration_ms": 65000},
            },
            headers=headers,
        )
        assert r.status_code == 200, f"环节① 创建语音失败: {r.text}"
        body = r.json()["data"]
        cid = body["id"]

        # ── 环节② AI 管线：进程内直调 process_content（mock ASR + 分类替身）──
        result = pipeline_mod.process_content(cid)
        assert result["status"] == "done", f"环节② 管线未 done: {result}"

        # ── 环节③ AI 打标：进程内直调 tag_content（monkeypatch 后回填 tags_json）──
        tag_result = pipeline_mod.tag_content(cid)
        assert tag_result["status"] == "succeeded", f"环节③ 打标失败: {tag_result}"

        # ── 环节④ 数据补齐：模拟分片 complete 路径回填 size_bytes（契约偏差①）──
        _backfill_size_bytes(db, cid, len(wav))

        yield {
            "user_id": user_id,
            "headers": headers,
            "phone": phone,
            "content_id": cid,
            "wav_len": len(wav),
        }

        _wipe_user(db, user_id)

    # ---- 环节① 上传 ----

    def test_e1_create_voice(self, journey, client):
        """上传：voice 内容创建即回显 duration（ms→s）/remark/taken_at"""
        r = client.get(
            "/api/v1/contents", params={"content_id": journey["content_id"]},
            headers=journey["headers"],
        )
        assert r.status_code == 200, r.text
        items = r.json()["data"]["items"]
        assert len(items) == 1, f"应恰 1 条: {len(items)}"
        d = items[0]
        assert d["content_type"] == "voice", f"类型应为 voice: {d['content_type']}"
        assert d["duration"] == 65, f"duration 应按 duration_ms//1000=65: {d['duration']}"
        assert d["remark"] == "外婆的叮嘱", f"remark 应即存即显: {d['remark']!r}"
        assert d["taken_at"] is not None, "taken_at 应随创建出参回显"

    # ---- 环节② AI 管线 ----

    def test_e2_pipeline_done_with_asr_text(self, journey, db):
        """DB 回查：status=done + mock ASR 文本回填 + 分类标签 + duration/taken_at 落库"""
        row = db.get(Content, journey["content_id"])
        assert row is not None, "内容行应存在"
        assert row.status == "done", f"status 应 done: {row.status}"
        assert row.text == _MOCK_ASR_TEXT, f"ASR mock 文本应回填: {row.text!r}"
        assert row.content_class == "emotion", f"分类应回填 emotion: {row.content_class}"
        assert row.duration == 65, f"DB duration 应 65: {row.duration}"
        assert row.taken_at is not None, "DB taken_at 应落库"

    # ---- 环节③ AI 打标 ----

    def test_e3_tags_json_backfill(self, journey, db):
        """DB 回查：tags_json 回填三个标签（monkeypatch generate_tags 后直调）"""
        db.expire_all()  # tag_content 走独立会话提交，本会话需失效缓存重读
        row = db.get(Content, journey["content_id"])
        assert row.tags_json == _VOICE_TAGS, f"tags_json 应回填: {row.tags_json!r}"

    # ---- 环节④ 详情 ----

    def test_e4_detail_via_query_param(self, journey, client, db):
        """详情：GET /contents?content_id= 精确取单条（契约偏差③：无 /{id} 详情路由）"""
        r = client.get(
            "/api/v1/contents", params={"content_id": journey["content_id"]},
            headers=journey["headers"],
        )
        assert r.status_code == 200, r.text
        d = r.json()["data"]["items"]
        assert len(d) == 1, f"content_id 精确查询应恰 1 条: {len(d)}"
        assert d[0]["text"] == _MOCK_ASR_TEXT, f"详情应带转写文本: {d[0]['text']!r}"
        assert d[0]["tags_json"] == _VOICE_TAGS, f"详情应带标签: {d[0]['tags_json']}"
        assert d[0]["status"] == "done", f"详情状态应 done: {d[0]['status']}"
        db_row = db.get(Content, journey["content_id"])
        assert db_row.size_bytes == journey["wav_len"], "DB size_bytes 应已补齐"

    # ---- 环节⑤ 备注 ----

    def test_e5_patch_remark_and_extra_forbid(self, journey, client):
        """备注：PATCH remark 生效；越权字段（extra=forbid）→ 422"""
        cid = journey["content_id"]
        headers = journey["headers"]
        r = client.patch(f"/api/v1/contents/{cid}", json={"remark": "改后的备注"}, headers=headers)
        assert r.status_code == 200, f"PATCH remark 失败: {r.text}"
        assert r.json()["data"]["remark"] == "改后的备注", "remark 应更新"
        r2 = client.patch(
            f"/api/v1/contents/{cid}", json={"remark": "x", "status": "done"}, headers=headers
        )
        assert r2.status_code == 422, f"越权字段应 422: {r2.text}"

    # ---- 环节⑥ 收藏 ----

    def test_e6_favorite_and_favorites_list(self, journey, client):
        """收藏：POST favorite → true；favorites 列表含该内容"""
        cid = journey["content_id"]
        headers = journey["headers"]
        r = client.post(f"/api/v1/contents/{cid}/favorite", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["favorite"] is True, "收藏态应为 true"
        r2 = client.get("/api/v1/favorites", headers=headers)
        assert r2.status_code == 200, r2.text
        fav_ids = [item["id"] for item in r2.json()["data"]]
        assert cid in fav_ids, f"收藏列表应含该内容: {fav_ids}"

    # ---- 环节⑦ 回收站 ----

    def test_e7_soft_delete_trash_restore(self, journey, client):
        """回收站：软删 → 列表消失 → trash days_left=30 → restore 回到正常域"""
        cid = journey["content_id"]
        headers = journey["headers"]
        r = client.delete(f"/api/v1/contents/{cid}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["deleted"] is True, "软删应成功"
        r2 = client.get("/api/v1/contents", params={"content_id": cid}, headers=headers)
        assert r2.json()["data"]["items"] == [], "软删后列表应不可见"
        r3 = client.get("/api/v1/trash", headers=headers)
        assert r3.status_code == 200, r3.text
        trash = r3.json()["data"]
        assert len(trash) == 1, f"回收站应恰 1 条: {len(trash)}"
        assert trash[0]["days_left"] == 30, f"刚删条目保留期应 30 天: {trash[0]['days_left']}"
        r4 = client.post(f"/api/v1/trash/{cid}/restore", headers=headers)
        assert r4.status_code == 200, r4.text
        assert r4.json()["data"]["deleted"] is False, "恢复应成功"
        r5 = client.get("/api/v1/contents", params={"content_id": cid}, headers=headers)
        assert len(r5.json()["data"]["items"]) == 1, "恢复后应回到正常内容域"

    # ---- 环节⑧ 波形 ----

    def test_e8_waveform_known_peaks(self, journey, client):
        """波形：8 桶已知峰值（12800/32768≈0.391、16384/32768=0.5，bb3 同款断言）"""
        r = client.get(
            f"/api/v1/contents/{journey['content_id']}/waveform",
            params={"buckets": 8},
            headers=journey["headers"],
        )
        assert r.status_code == 200, r.text
        buckets = r.json()["data"]["buckets"]
        assert len(buckets) == 8, f"桶数应 8: {len(buckets)}"
        assert buckets[0] == pytest.approx(12800 / 32768, abs=1e-3), f"桶0 峰值异常: {buckets[0]}"
        assert buckets[1] == pytest.approx(16384 / 32768, abs=1e-3), f"桶1 峰值异常: {buckets[1]}"
        assert all(0.0 <= v <= 1.0 for v in buckets), "峰值应归一 0..1"

    # ---- 环节⑨ 个人信息掩码 ----

    def test_e9_phone_masked_no_leak(self, journey, client):
        """掩码：/users/me 下发 138****XXXX；响应 JSON 任何位置不得含完整手机号明文"""
        r = client.get("/api/v1/users/me", headers=journey["headers"])
        assert r.status_code == 200, r.text
        payload = r.json()
        me = payload["data"]
        assert me["id"] == journey["user_id"], "应返回当前用户"
        assert me["phone"] == "138****" + journey["phone"][7:], f"手机号应掩码: {me['phone']!r}"
        assert journey["phone"] not in json.dumps(payload, ensure_ascii=False), (
            f"响应泄漏完整手机号: {payload}"
        )

    # ---- 环节⑩ 用量统计 ----

    def test_e10_stats_total_bytes(self, journey, client):
        """统计：daily-summary total_bytes = 语音原件大小（size_bytes 补齐后）"""
        r = client.get("/api/v1/stats/daily-summary", headers=journey["headers"])
        assert r.status_code == 200, r.text
        summary = r.json()["data"]
        assert summary["total_bytes"] == journey["wav_len"], (
            f"total_bytes 应=语音原件 {journey['wav_len']}: {summary['total_bytes']}"
        )

    # ---- 环节⑪ 时间轴 ----

    def test_e11_timeline_queryable(self, journey, client):
        """时间轴：单条语音（无 L2/L3 事件）→ timeline 200 + data 为 list（不造假不报错）"""
        r = client.get("/api/v1/events/timeline", headers=journey["headers"])
        assert r.status_code == 200, r.text
        assert isinstance(r.json()["data"], list), "timeline data 应为列表"


# ---------------------------------------------------------------------------
# S2 时间胶囊全生命周期
# ---------------------------------------------------------------------------


class TestS2CapsuleLifecycle:
    """S2 场景：封存一段记忆给未来的自己 → 到期提醒 → 开启回看 / 反悔撤销"""

    @pytest.fixture()
    def capsule(self, client, auth_headers, db):
        """公共前奏：用户 + 文本内容 + 封存（+1h，带 note）→ 快照"""
        user_id, headers = auth_headers("s2")
        content_id = _mk_text(client, headers, "S2 给一年后的自己")
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        r = client.post(
            "/api/v1/capsules",
            json={"content_id": content_id, "open_at": future, "note": "给未来的我"},
            headers=headers,
        )
        assert r.status_code == 200, f"封存失败: {r.text}"
        cap = r.json()["data"]
        snap = {
            "user_id": user_id,
            "headers": headers,
            "content_id": content_id,
            "cap_id": cap["id"],
        }
        yield snap
        _wipe_user(db, user_id, capsules_too=True)

    def _force_due(self, db, cap_id: str) -> None:
        """直改库到期（模拟时间流逝）+ 复位 30s 节流窗口（先例 ba2 同款）"""
        db.execute(
            text(
                "UPDATE capsules SET open_at = now() - interval '5 minutes' WHERE id = :cid"
            ),
            {"cid": cap_id},
        )
        db.commit()
        capsule_scan_mod._last_scan_ts["ts"] = 0.0

    # ---- 环节① 封存落库 ----

    def test_seal_persists_row(self, capsule, client, db):
        """封存：出参 sealed/note/opened_at=None；DB 行 user/content/note 全对"""
        r = client.get("/api/v1/capsules", headers=capsule["headers"])
        assert r.status_code == 200, r.text
        items = r.json()["data"]
        assert len(items) == 1, f"列表应恰 1 条: {len(items)}"
        item = items[0]
        assert item["status"] == "sealed", f"应 sealed: {item['status']}"
        assert item["content_id"] == capsule["content_id"], "content_id 应对应"
        assert item["note"] == "给未来的我", f"note 应保留: {item['note']!r}"
        assert item["opened_at"] is None, "未开启 opened_at 应为 None"
        row = db.get(Capsule, capsule["cap_id"])
        assert row is not None, "DB 胶囊行应存在"
        assert str(row.content_id) == capsule["content_id"], "DB content_id 应对应"
        assert row.status == "sealed", f"DB 状态应 sealed: {row.status}"

    # ---- 环节② 列表摘要 ----

    def test_list_carries_content_brief(self, capsule, client):
        """列表：附 content 摘要（id/content_type/text 口径，ContentOut 投影）"""
        r = client.get("/api/v1/capsules", headers=capsule["headers"])
        items = r.json()["data"]
        brief = items[0]["content"]
        assert brief is not None, "列表应附 content 摘要"
        assert brief["id"] == capsule["content_id"], f"摘要 id 应对应: {brief['id']}"
        assert brief["content_type"] == "text", f"摘要类型应 text: {brief['content_type']}"
        assert brief["text"] == "S2 给一年后的自己", f"摘要 text 应回传: {brief['text']!r}"

    # ---- 环节③ 未到期开启被拒 ----

    def test_open_before_due_409_with_remaining_days(self, capsule, client):
        """未到期开启 → 409 CAPSULE_003 + details.remaining_days>=1 + message 含「剩余」"""
        r = client.post(f"/api/v1/capsules/{capsule['cap_id']}/open", headers=capsule["headers"])
        assert r.status_code == 409, f"未到期应 409: {r.text}"
        body = r.json()
        assert body["code"] == "CAPSULE_003", f"错误码应 CAPSULE_003: {body['code']}"
        assert body["details"]["remaining_days"] >= 1, f"应报剩余天数: {body['details']}"
        assert "剩余" in body["message"], f"message 应含「剩余」: {body['message']}"

    # ---- 环节④ 到期惰性扫描 + 消息建发 ----

    def test_lazy_scan_marks_due_and_notifies(self, capsule, client, db):
        """到期：节流归零后 GET /capsules 惰性扫描 → 置 due + capsule_due 消息
        （content_id 列与 payload 双断言）"""
        self._force_due(db, capsule["cap_id"])
        r = client.get("/api/v1/capsules", headers=capsule["headers"])
        assert r.status_code == 200, r.text
        items = r.json()["data"]
        assert items[0]["status"] == "due", f"惰性扫描应置 due: {items[0]['status']}"
        r2 = client.get("/api/v1/messages", headers=capsule["headers"])
        due_msgs = [m for m in r2.json()["data"]["items"] if m["msg_type"] == "capsule_due"]
        assert len(due_msgs) == 1, f"到期消息应恰 1 条: {len(due_msgs)}"
        assert due_msgs[0]["content_id"] == capsule["content_id"], (
            f"消息 content_id 列应对应: {due_msgs[0]['content_id']}"
        )
        assert (due_msgs[0]["payload"] or {}).get("content_id") == capsule["content_id"], (
            f"消息 payload 应带 content_id: {due_msgs[0]['payload']}"
        )
        row = db.get(Capsule, capsule["cap_id"])
        assert row.status == "due", f"DB 状态应流转 due: {row.status}"

    # ---- 环节⑤ 扫描幂等 ----

    def test_scan_idempotent_no_duplicate_message(self, capsule, client, db):
        """幂等：再次节流归零扫描 → 不重复产消息（status 已流转不再命中）"""
        self._force_due(db, capsule["cap_id"])
        client.get("/api/v1/capsules", headers=capsule["headers"])  # 第一轮扫描
        capsule_scan_mod._last_scan_ts["ts"] = 0.0
        client.get("/api/v1/capsules", headers=capsule["headers"])  # 第二轮扫描
        r = client.get("/api/v1/messages", headers=capsule["headers"])
        due_msgs = [m for m in r.json()["data"]["items"] if m["msg_type"] == "capsule_due"]
        assert len(due_msgs) == 1, f"二次扫描不应重复产消息: {len(due_msgs)}"

    # ---- 环节⑥ 到期开启 ----

    def test_open_after_due_returns_content(self, capsule, client, db):
        """开启：到期后 200 status=opened + opened_at 非空 + content 摘要回传；重复开启幂等"""
        self._force_due(db, capsule["cap_id"])
        r = client.post(f"/api/v1/capsules/{capsule['cap_id']}/open", headers=capsule["headers"])
        assert r.status_code == 200, f"到期开启失败: {r.text}"
        opened = r.json()["data"]
        assert opened["status"] == "opened", f"应 opened: {opened['status']}"
        assert opened["opened_at"] is not None, "opened_at 应落值"
        assert opened["content"]["id"] == capsule["content_id"], "开启应回传 content"
        assert opened["content"]["text"] == "S2 给一年后的自己", "开启应回传记忆正文"
        r2 = client.post(f"/api/v1/capsules/{capsule['cap_id']}/open", headers=capsule["headers"])
        assert r2.status_code == 200 and r2.json()["data"]["status"] == "opened", "重复开启应幂等"
        db.expire_all()
        row = db.get(Capsule, capsule["cap_id"])
        assert row.status == "opened", f"DB 状态应 opened: {row.status}"

    # ---- 环节⑦ 已开启不可撤销 ----

    def test_delete_opened_409(self, capsule, client, db):
        """撤销：已开启的胶囊 → 409 CAPSULE_004（时间胶囊不可抵赖）"""
        self._force_due(db, capsule["cap_id"])
        r = client.post(f"/api/v1/capsules/{capsule['cap_id']}/open", headers=capsule["headers"])
        assert r.status_code == 200, r.text
        r2 = client.delete(f"/api/v1/capsules/{capsule['cap_id']}", headers=capsule["headers"])
        assert r2.status_code == 409, f"已开启撤销应 409: {r2.text}"
        assert r2.json()["code"] == "CAPSULE_004", f"错误码应 CAPSULE_004: {r2.json()['code']}"

    # ---- 环节⑧ 撤销封存 ----

    def test_cancel_sealed_keeps_first(self, capsule, client):
        """撤销：未开启可删（列表只剩第一条；出参 deleted=true）"""
        content_id2 = _mk_text(client, capsule["headers"], "S2 第二条封存")
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        r = client.post(
            "/api/v1/capsules",
            json={"content_id": content_id2, "open_at": future},
            headers=capsule["headers"],
        )
        assert r.status_code == 200, r.text
        cap_id2 = r.json()["data"]["id"]
        r2 = client.delete(f"/api/v1/capsules/{cap_id2}", headers=capsule["headers"])
        assert r2.status_code == 200, f"撤销失败: {r2.text}"
        assert r2.json()["data"]["deleted"] is True, "撤销应成功"
        r3 = client.get("/api/v1/capsules", headers=capsule["headers"])
        remain = r3.json()["data"]
        assert len(remain) == 1, f"撤销后列表应只剩 1 条: {len(remain)}"
        assert remain[0]["id"] == capsule["cap_id"], "剩余应为第一条胶囊"


# ---------------------------------------------------------------------------
# S3 聚合与时间轴
# ---------------------------------------------------------------------------


class TestS3Aggregation:
    """S3 场景：连续几天录同一类话题 → 聚成主题流 → 新录音自动跟上 →
    用户确认后算法不再动（R9-C 回归内嵌）"""

    @pytest.fixture()
    def journey(self, client, auth_headers, db):
        """公共前奏：3 条语音（base-2d ×2、base-1d ×1）同 content_class=mixed
        （7 天窗 ≥3 次跨 2 天 → 首轮聚合建 L3）→ 快照"""
        user_id, headers = auth_headers("s3")
        base = datetime.now(timezone.utc) - timedelta(hours=2)
        ids1 = [
            _mk_voice(client, headers, user_id, base - timedelta(days=2)),
            _mk_voice(client, headers, user_id, base - timedelta(days=2) + timedelta(hours=2)),
            _mk_voice(client, headers, user_id, base - timedelta(days=1)),
        ]
        for cid in ids1:
            _set_content_class(db, cid, "mixed")

        agg1 = aggregate_user(db, user_id)
        assert agg1.get("upper_items") == 3, f"首轮应挂接 3 成员: {agg1}"

        ev = db.execute(
            select(Event).where(Event.user_id == user_id, Event.level == 3)
        ).scalars().one()
        snap = {
            "user_id": user_id,
            "headers": headers,
            "ids1": ids1,
            "event_id": str(ev.id),
            "base": base,
        }
        yield snap
        _wipe_user(db, user_id)

    # ---- 环节① L3 建立 ----

    def test_l3_created_with_three_members(self, journey, db):
        """首轮：L3「标签 · mixed」draft + 恰 3 成员（EventItem 直查）"""
        ev = db.get(Event, journey["event_id"])
        assert ev.title == "标签 · mixed", f"L3 标题应对应: {ev.title!r}"
        assert ev.level == 3 and ev.status == "draft", f"级别/状态: {ev.level}/{ev.status}"
        members = db.execute(
            select(EventItem.content_id).where(EventItem.event_id == ev.id)
        ).scalars().all()
        assert sorted(str(m) for m in members) == sorted(journey["ids1"]), (
            f"成员应恰为前 3 条: {members}"
        )

    # ---- 环节② 增量并入（R9-C 回归）----

    def test_incremental_merge_keeps_all_members(self, journey, client, db):
        """R9-C：+2 条同标签 → 全部并入已有 L3（5 成员不丢）+ end_time 外延 + 事件数仍 1"""
        base = journey["base"]
        ids2 = [
            _mk_voice(client, journey["headers"], journey["user_id"], base),
            _mk_voice(client, journey["headers"], journey["user_id"], base + timedelta(minutes=5)),
        ]
        for cid in ids2:
            _set_content_class(db, cid, "mixed")
        agg2 = aggregate_user(db, journey["user_id"])
        assert agg2.get("upper_items") == 2, f"增量应挂接 2 条: {agg2}"
        db.expire_all()
        events = db.execute(
            select(Event).where(
                Event.user_id == journey["user_id"], Event.level == 3, Event.deleted_at.is_(None)
            )
        ).scalars().all()
        assert len(events) == 1, f"level-3 事件应仍 1 个（不重建流）: {len(events)}"
        ev = events[0]
        members = db.execute(
            select(EventItem.content_id).where(EventItem.event_id == ev.id)
        ).scalars().all()
        member_ids = sorted(str(m) for m in members)
        assert member_ids == sorted(journey["ids1"] + ids2), (
            f"R9-C 回归：5 成员必须全部挂接不丢: {member_ids}"
        )
        assert ev.end_time is not None and ev.end_time >= base, (
            f"end_time 应外延到新成员时间: {ev.end_time} vs {base}"
        )

    # ---- 环节③ 时间轴出参 ----

    def test_timeline_carries_count_and_voice(self, journey, client, db):
        """时间轴：timeline 出参 content_count=5 + 单条 voice VoiceInfo
        （契约偏差④：voice 是单对象非计数字段；偏差⑤：duration 暂不下发恒 None，
        客户端播放时由 InnerAudioContext.duration 补齐——见 _batch_event_voices）"""
        base = journey["base"]
        added = []
        for offset in (timedelta(0), timedelta(minutes=5)):
            cid = _mk_voice(client, journey["headers"], journey["user_id"], base + offset)
            _set_content_class(db, cid, "mixed")
            added.append(cid)
        agg = aggregate_user(db, journey["user_id"])
        assert agg.get("upper_items") == 2, f"增量并入应完成: {agg}"
        r = client.get("/api/v1/events/timeline", headers=journey["headers"])
        assert r.status_code == 200, r.text
        target = next(
            (e for e in r.json()["data"] if e["id"] == journey["event_id"]), None
        )
        assert target is not None, f"timeline 应含 L3 事件: {[e['id'] for e in r.json()['data']]}"
        assert target["content_count"] == 5, f"content_count 应 5: {target['content_count']}"
        voice = target["voice"]
        assert voice is not None, "timeline 应带单条 voice（就地播放）"
        assert voice["content_id"] in (journey["ids1"] + added), (
            f"voice.content_id 应为流成员: {voice['content_id']}"
        )
        assert voice["title"] == "语音记忆", f"无转写文本应回退「语音记忆」: {voice['title']!r}"

    # ---- 环节④ confirmed 保护 ----

    def test_confirmed_protects_stream_from_new_members(self, journey, client, db):
        """confirmed：用户确认（带 title）后新同标签语音不再并入（成员恒 5，c6 零挂接）"""
        base = journey["base"]
        ids2 = [
            _mk_voice(client, journey["headers"], journey["user_id"], base),
            _mk_voice(client, journey["headers"], journey["user_id"], base + timedelta(minutes=5)),
        ]
        for cid in ids2:
            _set_content_class(db, cid, "mixed")
        agg2 = aggregate_user(db, journey["user_id"])
        assert agg2.get("upper_items") == 2, f"确认前应 5 成员: {agg2}"

        r = client.post(
            "/api/v1/events/confirm",
            json={"event_id": journey["event_id"], "title": "标签 · mixed"},
            headers=journey["headers"],
        )
        assert r.status_code == 200, f"确认失败: {r.text}"
        out = r.json()["data"]
        assert out["status"] == "confirmed", f"应 confirmed: {out['status']}"
        assert out["title_source"] == "user", f"title_source 应 user: {out['title_source']}"
        db.expire_all()
        ev = db.get(Event, journey["event_id"])
        assert ev.status == "confirmed" and ev.title_source == "user", "DB 确认态应落库"

        # 确认后新增第 6 条同标签语音 → 算法不得动用户背书的流
        c6 = _mk_voice(client, journey["headers"], journey["user_id"], base + timedelta(hours=2))
        _set_content_class(db, c6, "mixed")
        agg3 = aggregate_user(db, journey["user_id"])
        assert agg3.get("upper_items") == 0, f"confirmed 保护：新成员不得并入: {agg3}"
        members = db.execute(
            select(EventItem.content_id).where(EventItem.event_id == ev.id)
        ).scalars().all()
        member_ids = sorted(str(m) for m in members)
        assert member_ids == sorted(journey["ids1"] + ids2), (
            f"成员应恒 5（c6 零挂接）: {member_ids}"
        )
        assert str(c6) not in member_ids, "第 6 条不得挂入 confirmed 流"


# ---------------------------------------------------------------------------
# S4 越权与边界矩阵
# ---------------------------------------------------------------------------


class TestS4AuthzMatrix:
    """S4 场景：两个用户——B 拿 A 的内容 id 四路操作全被 404 拒绝；
    参数边界（buckets）/类型边界（非语音波形）/数据边界（非 11 位手机号）"""

    @pytest.fixture()
    def matrix(self, client, auth_headers, db):
        """双用户 + A 的语音/文本内容 → 快照（B 手机号 10086：非 11 位）"""
        user_a, headers_a = auth_headers("s4a")
        user_b, headers_b = auth_headers("s4b")
        _update_user_phone(db, user_b, "10086")

        key = _seed_wav_storage(user_a, _wav_bytes([800] * 32))
        r = client.post(
            "/api/v1/contents",
            json={
                "content_type": "voice",
                "cos_key": key,
                "source": "app",
                "extra": {"duration_ms": 10000},
            },
            headers=headers_a,
        )
        assert r.status_code == 200, r.text
        voice_id = r.json()["data"]["id"]
        text_id = _mk_text(client, headers_a, "S4 纯文本内容（无音频）")
        snap = {
            "user_a": user_a,
            "user_b": user_b,
            "headers_a": headers_a,
            "headers_b": headers_b,
            "voice_id": voice_id,
            "text_id": text_id,
        }
        yield snap
        _wipe_user(db, user_a)
        _wipe_user(db, user_b)

    def test_cross_user_four_routes_404(self, matrix, client):
        """IDOR：B 对 A 内容 favorite/PATCH/waveform/软删四路全 404 CONTENT_010"""
        vid = matrix["voice_id"]
        hb = matrix["headers_b"]
        r1 = client.post(f"/api/v1/contents/{vid}/favorite", headers=hb)
        assert r1.status_code == 404, f"收藏越权应 404: {r1.status_code}"
        assert r1.json()["code"] == "CONTENT_010", f"错误码: {r1.json()['code']}"
        r2 = client.patch(f"/api/v1/contents/{vid}", json={"remark": "篡改"}, headers=hb)
        assert r2.status_code == 404, f"改备注越权应 404: {r2.status_code}"
        assert r2.json()["code"] == "CONTENT_010"
        r3 = client.get(f"/api/v1/contents/{vid}/waveform", headers=hb)
        assert r3.status_code == 404, f"波形越权应 404: {r3.status_code}"
        assert r3.json()["code"] == "CONTENT_010"
        r4 = client.delete(f"/api/v1/contents/{vid}", headers=hb)
        assert r4.status_code == 404, f"软删越权应 404: {r4.status_code}"
        assert r4.json()["code"] == "CONTENT_010"

    def test_waveform_buckets_out_of_range_422(self, matrix, client):
        """参数边界：buckets=0 / 129 → 422（Query ge=1 le=128）"""
        for bad in (0, 129):
            r = client.get(
                f"/api/v1/contents/{matrix['voice_id']}/waveform",
                params={"buckets": bad},
                headers=matrix["headers_a"],
            )
            assert r.status_code == 422, f"buckets={bad} 应 422: {r.status_code}"

    def test_waveform_text_content_404_media(self, matrix, client):
        """类型边界：text 内容请求波形 → 404 MEDIA_003（音频不可用，显式错误码）"""
        r = client.get(
            f"/api/v1/contents/{matrix['text_id']}/waveform", headers=matrix["headers_a"]
        )
        assert r.status_code == 404, f"text 波形应 404: {r.status_code}"
        assert r.json()["code"] == "MEDIA_003", f"错误码应 MEDIA_003: {r.json()['code']}"

    def test_phone_mask_none_for_non_11_digits(self, matrix, client):
        """数据边界：非 11 位手机号（10086）→ 掩码 null 不下发；响应不含原始号码"""
        r = client.get("/api/v1/users/me", headers=matrix["headers_b"])
        assert r.status_code == 200, r.text
        payload = r.json()
        assert payload["data"]["phone"] is None, f"非 11 位应掩码 null: {payload['data']['phone']}"
        assert "10086" not in json.dumps(payload, ensure_ascii=False), "响应不得含原始号码"


# ---------------------------------------------------------------------------
# S5 回响与消息链
# ---------------------------------------------------------------------------


class TestS5EchoChain:
    """S5 场景：去年今天写过一段话 → 今天打开 App 看到回响 → 消息中心可查"""

    @pytest.fixture()
    def chain(self, auth_headers, db):
        """用户 + DB 直插去年今日内容（text/正常/done，先例 _last_year_content 同款）"""
        user_id, headers = auth_headers("s5")
        c = Content(
            id=str(uuid.uuid4()),
            user_id=user_id,
            content_type="text",
            text="S5 回响断言：去年今日写下的这段话",
            taken_at=_last_year_taken_at(),
            sensitive_status="正常",
            status="done",
        )
        db.add(c)
        db.commit()
        snap = {"user_id": user_id, "headers": headers, "content_id": str(c.id)}
        yield snap
        _wipe_user(db, user_id)

    def test_echo_hit_creates_history_and_message(self, chain, db):
        """建发：get_today_echo 命中即建发——EchoHistory 恰 1 + echo 消息恰 1
        （payload 与 content_id 列双断言，R9-B3 守卫加列校验）"""
        resp = echo_svc.get_today_echo(db, chain["user_id"])
        assert resp is not None and resp["content_id"] == chain["content_id"], (
            f"回响应命中去年今日内容: {resp}"
        )
        hist = db.execute(
            select(EchoHistory).where(EchoHistory.user_id == chain["user_id"])
        ).scalars().all()
        assert len(hist) == 1, f"EchoHistory 应恰 1 条: {len(hist)}"
        msgs = db.execute(
            select(Message).where(
                Message.user_id == chain["user_id"], Message.msg_type == "echo"
            )
        ).scalars().all()
        assert len(msgs) == 1, f"echo 消息应恰 1 条: {len(msgs)}"
        assert (msgs[0].payload or {}).get("content_id") == chain["content_id"], (
            f"payload 应带 content_id: {msgs[0].payload}"
        )
        assert str(msgs[0].content_id) == chain["content_id"], (
            f"content_id 列应直存: {msgs[0].content_id}"
        )

    def test_messages_out_contract(self, chain, client, db):
        """消息中心：GET /messages 出参字段契约 + echo 消息 content_id 直出"""
        echo_svc.get_today_echo(db, chain["user_id"])
        r = client.get("/api/v1/messages", headers=chain["headers"])
        assert r.status_code == 200, r.text
        items = r.json()["data"]["items"]
        echo_items = [m for m in items if m["msg_type"] == "echo"]
        assert len(echo_items) == 1, f"消息中心应含恰 1 条 echo: {len(echo_items)}"
        m = echo_items[0]
        expected_fields = (
            "id", "channel", "msg_type", "title", "body", "payload",
            "content_id", "status", "sent_at", "read_at",
        )
        for field in expected_fields:
            assert field in m, f"MessageOut 缺字段 {field}: {sorted(m.keys())}"
        assert m["content_id"] == chain["content_id"], f"content_id 应直出: {m['content_id']}"
        assert m["payload"].get("content_id") == chain["content_id"], "payload 应带 content_id"
        assert m["channel"] == "in_app", f"回响应 in-app 通道: {m['channel']}"
        assert m["sent_at"] is not None, "sent_at 应有值"
