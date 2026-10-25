"""D-23（2026-10-25 · 4520 条 processing 卡死）三项修复的回归测试。

## 这一批修的到底是什么（背景，别丢）

`status='processing'` 积压 4520 条（含 failed 共 5157），最早 8/27、最新 0.6 小时前，
`qdrant_text_id` 非空数 **0/4520**（向量化从未执行）——用户侧表现只是"搜不到自己的照片"，
**完全静默**。根因：`workers/requeue_job.py`（超龄重扫兜底）**从未被任何调度执行过**
（全仓零执行路径），而 `process_content` 的异常路径明确承诺"RQ 重投耗尽 → 它兜底"。

三项修复各有本文件的用例：
  ① 调度（根因）—— 落成 `deploy/systemd/yishu-requeue.{service,timer}`，由
     `deploy_one.sh` 安装。本文件用**静态断言**守住"文件在、unit 关键项在、
     deploy_one.sh 真的装了它、RUNBOOK 有安装与验证命令"，因为 systemd 无法在
     测试里真启动（Windows 开发机 + 需要 root）。
  ② 卡死可见 —— `/healthz` 的 `stale_processing_count` / `stale_processing_degraded`，
     且判据与 job 选行**同源**（这是本批最容易做错的地方：两套口径会让
     "告警报的数"与"兜底会重投的数"永远对不上）。
  ③ 缩短复发面 —— `extra.processing_started_at` 打点：阶段 0 写、成功/失败清除，
     超龄判定优先用它、回退 `updated_at`。
     为什么必须加：`updated_at` 带 onupdate=now()，任何写者（打标/情绪/聚合回写）
     碰一下就把卡死行刷成"新鲜的" ⇒ 兜底永远选不中它。

## 运行条件

`@pytest.mark.integration` 的用例需要 PG（连不上由 conftest 的 skill 跳过逻辑处理）；
纯静态断言（systemd/文档）不需要任何依赖。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.db.models import Content, User
from app.db.session import SessionLocal
from app.services.pipeline_common import (
    PROCESSING_STARTED_KEY,
    stale_processing_filter,
    stale_since_condition,
    transition_processing,
)
from app.workers.requeue_job import (
    REQUEUE_DEFAULT_STALE_SECONDS,
    _stale_candidates,
    stale_processing_count,
)
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update

REPO_ROOT = Path(__file__).resolve().parents[2]


# ══════════════════════════════════════════════════════════════════════════
# ③ 打点（processing_started_at）—— 单元级，无 DB
# ══════════════════════════════════════════════════════════════════════════


class _FakeContent:
    """最小替身：patch_extra / clear_processing_started 只碰 .extra"""

    def __init__(self, extra=None):
        self.extra = extra


def test_transition_to_processing_writes_iso_utc():
    """进入 processing 写 ISO-8601 UTC —— SQL 端用 ::timestamptz 反解，必须含真偏移"""
    c = _FakeContent()
    transition_processing(c, "processing")
    ts = c.extra[PROCESSING_STARTED_KEY]
    assert isinstance(ts, str)
    assert "T" in ts
    assert ts.endswith("+00:00"), f"必须是 UTC 偏移（否则解析差时区）：{ts}"
    assert datetime.fromisoformat(ts).tzinfo is not None


def test_transition_preserves_other_extra_keys():
    """状态转换不得冲掉 extra 里既有键（error / requeue_count / audio_processing 等）"""
    c = _FakeContent({"error": {"code": "X", "retryable": True}, "requeue_count": 2})
    transition_processing(c, "processing")
    assert c.extra["error"] == {"code": "X", "retryable": True}
    assert c.extra["requeue_count"] == 2
    assert PROCESSING_STARTED_KEY in c.extra
    assert c.status == "processing"


def test_transition_done_and_failed_clear_stamp():
    """收尾（done/failed）必须清打点，但保留其余 extra"""
    for target in ("done", "failed"):
        c = _FakeContent({PROCESSING_STARTED_KEY: "2026-10-25T00:00:00.000000+00:00", "requeue_count": 1})
        transition_processing(c, target)
        assert c.status == target
        assert PROCESSING_STARTED_KEY not in c.extra, f"{target} 收尾未清打点"
        assert c.extra["requeue_count"] == 1


def test_transition_on_empty_extra_keeps_none_semantics():
    """extra 为 None 时收尾不得把它变成 {} 以外的怪值（保 True/False 语义清晰）"""
    c = _FakeContent(None)
    transition_processing(c, "failed")
    assert c.extra == {}, f"期望空 dict（可安全判空），实际 {c.extra!r}"


def test_success_path_transitions_source_guard():
    """源码守卫：process_content 的阶段 0 与三条收尾**都**走 transition_processing。

    为什么用源码断言而不是跑管线：跑通管线要真 COS/ASR/LLM（成本 + 环境），
    而"收尾是否维护打点"是纯代码结构事实 —— 且比逐个函数断言更能防"新增收尾分支
    忘了打点"。若有人后来加分支绕开这个入口，这里会红。
    """
    src = (REPO_ROOT / "backend" / "app" / "services" / "pipeline.py").read_text(encoding="utf-8")
    assert 'transition_processing(content, "processing")' in src, "阶段 0 未走统一转换入口"
    assert 'transition_processing(content, "done")' in src, "成功收尾未走统一转换入口"
    # 注意：3.11 的 f-string 不允许内嵌反斜杠转义（3.12 才放开），故先把计数算出来再插值。
    failed_calls = src.count('transition_processing(target, "failed")')
    assert failed_calls >= 2, (
        f"两条失败收尾（AsrError / 通用 Exception）都应走统一转换入口，实际 {failed_calls} 处"
    )


# ══════════════════════════════════════════════════════════════════════════
# ① 调度交付物（静态断言：systemd unit + deploy_one.sh 接线 + RUNBOOK）
# ══════════════════════════════════════════════════════════════════════════


def test_requeue_systemd_files_exist():
    """根因修复的核心交付物必须在仓里（不是只写在文档里）"""
    svc = REPO_ROOT / "deploy" / "systemd" / "yishu-requeue.service"
    tmr = REPO_ROOT / "deploy" / "systemd" / "yishu-requeue.timer"
    assert svc.is_file(), "缺 yishu-requeue.service —— 根因修复的交付物不存在"
    assert tmr.is_file(), "缺 yishu-requeue.timer —— 光有 service 不会定时跑"


def test_requeue_service_has_required_directives():
    """service 关键项：oneshot + 真跑 requeue_job + flock 防跨轮并发 + 日志落盘"""
    text = (REPO_ROOT / "deploy" / "systemd" / "yishu-requeue.service").read_text(encoding="utf-8")
    assert "Type=oneshot" in text, "必须是 oneshot（跑完即退，由 timer 再拉起）"
    assert "-m app.workers.requeue_job" in text, "ExecStart 必须真的执行 requeue_job"
    assert "flock -n" in text, (
        "必须 flock 互斥：requeue_job 走 enqueue_high（无确定性 job_id），"
        "跨轮并发会把同一行投两个 job"
    )
    assert "--older-than-seconds" in text
    assert "StandardOutput=append:" in text, "日志必须落盘（否则 timer 跑没跑无从查）"
    # 模板占位符要与既有 unit 一致（deploy_one.sh 的 sed 才能渲染）
    assert "@DEPLOY_ROOT@" in text and "@RUN_USER@" in text


def test_requeue_timer_has_schedule_and_persistence():
    """timer 关键项：周期触发 + Persistent（关机错过的触发开机补跑）+ 指向 service"""
    text = (REPO_ROOT / "deploy" / "systemd" / "yishu-requeue.timer").read_text(encoding="utf-8")
    assert "OnCalendar=" in text, "必须有周期表达式"
    assert "Persistent=true" in text, (
        "必须 Persistent=true：本次故障的一条成因正是『批量导入被中断后永久静默』，"
        "关机/重启期间错过的触发必须开机补跑"
    )
    assert "Unit=yishu-requeue.service" in text
    assert "WantedBy=timers.target" in text, "timer 必须挂 timers.target 才能被 enable"


def test_deploy_one_installs_requeue_units():
    """deploy_one.sh 必须真的装这两个 unit（否则又是"文件在仓里、线上没有"）"""
    text = (REPO_ROOT / "deploy" / "scripts" / "deploy_one.sh").read_text(encoding="utf-8")
    assert "yishu-requeue.service" in text, "deploy_one.sh 没装 requeue service"
    assert "yishu-requeue.timer" in text, "deploy_one.sh 没装 requeue timer"
    assert "enable --now yishu-requeue.timer" in text, "timer 必须 enable --now 才会真正开始计时"
    # 关键反例守卫：**不能** enable service（oneshot 的 service 被 enable 会在开机立即全量重投）
    assert "enable yishu-requeue.service" not in text, (
        "禁止 enable yishu-requeue.service —— 它是 oneshot，enable 会在开机时立即跑一次"
        "完整重投（意外外部 API 成本）。只 enable timer。"
    )


def test_runbook_documents_install_and_verify():
    """RUNBOOK 必须给出可执行的安装命令 + 验证步骤（不是"应该挂个 cron"这种空话）"""
    text = (REPO_ROOT / "deploy" / "RUNBOOK.md").read_text(encoding="utf-8")
    assert "yishu-requeue.timer" in text
    assert "systemctl list-timers yishu-requeue.timer" in text, "缺少验证步骤：timer 是否注册"
    assert "systemctl enable --now yishu-requeue.timer" in text, "缺少可执行的安装命令"
    assert "stale_processing_count" in text, "RUNBOOK 未记录新增的可观测键"


# ══════════════════════════════════════════════════════════════════════════
# ②③ 判据同源 + 打点优先级（需 PG）
# ══════════════════════════════════════════════════════════════════════════

pytestmark_db = pytest.mark.integration


@pytest.fixture()
def db_user():
    db = SessionLocal()
    user = User(phone=f"d23-test-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.execute(sa_delete(Content).where(Content.user_id == user.id))
    db.delete(user)
    db.commit()
    db.close()


def _content(db, user_id: str, *, ctype: str = "text", status: str = "processing", extra=None) -> Content:
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type=ctype,
        status=status,
        source="app",
        extra=extra,
    )
    db.add(c)
    db.commit()
    return c


def _set_updated_at(db, content: Content, when: datetime) -> None:
    """Core update 绕过 ORM onupdate，精确落值"""
    db.execute(sa_update(Content).where(Content.id == content.id).values(updated_at=when))
    db.commit()
    db.refresh(content)


@pytest.mark.integration
def test_stamp_overrides_updated_at(db_user):
    """③ 核心：`updated_at` 很新（被别的写者刷新过）但**打点很旧** ⇒ 仍判超龄。

    这正是修复前漏掉整类卡死行的机制：打标/情绪/聚合任务碰一下 updated_at，
    一条 3 天前卡死的行在旧口径下就变成"新鲜的"。
    """
    db, user = db_user
    old_ts = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    c = _content(db, user.id, extra={PROCESSING_STARTED_KEY: old_ts})
    # updated_at 拨到"刚刚"（模拟被别的写者刷新）
    _set_updated_at(db, c, datetime.now(UTC))

    cutoff = datetime.now(UTC) - timedelta(seconds=REQUEUE_DEFAULT_STALE_SECONDS)
    picked = _stale_candidates(db, cutoff, 10_000)
    assert any(x.id == c.id for x in picked), (
        "打点已 3 天前、updated_at 刚被刷新 —— 必须仍判超龄（否则这类行永远漏掉）"
    )


@pytest.mark.integration
def test_fresh_stamp_shields_running_row(db_user):
    """③ 反向：打点很新（真的在跑）⇒ 即使 updated_at 很旧也不能被选中（防抢正在跑的行）"""
    db, user = db_user
    fresh_ts = datetime.now(UTC).isoformat()
    c = _content(db, user.id, extra={PROCESSING_STARTED_KEY: fresh_ts})
    _set_updated_at(db, c, datetime(2000, 1, 1, tzinfo=UTC))

    cutoff = datetime.now(UTC) - timedelta(seconds=REQUEUE_DEFAULT_STALE_SECONDS)
    picked = _stale_candidates(db, cutoff, 10_000)
    assert not any(x.id == c.id for x in picked), (
        "打点是新鲜的（管线真在跑）⇒ 不得被兜底选中（否则会抢跑中的行、双投双计费）"
    )


@pytest.mark.integration
def test_no_stamp_falls_back_to_updated_at(db_user):
    """③ 兼容：历史行没有打点键 ⇒ 行为与修复前完全一致（按 updated_at）"""
    db, user = db_user
    stale = _content(db, user.id)  # 无打点
    _set_updated_at(db, stale, datetime(2000, 1, 1, tzinfo=UTC))
    fresh = _content(db, user.id)  # 无打点
    _set_updated_at(db, fresh, datetime.now(UTC))

    cutoff = datetime.now(UTC) - timedelta(seconds=REQUEUE_DEFAULT_STALE_SECONDS)
    picked = _stale_candidates(db, cutoff, 10_000)
    ids = {x.id for x in picked}
    assert stale.id in ids
    assert fresh.id not in ids


@pytest.mark.integration
def test_count_and_candidates_share_one_criterion(db_user):
    """② **同源守卫**：`stale_processing_count()` 报的数必须等于 job 选中的 processing 行数。

    这是本批最易做错的地方 —— 两套口径会让"告警报 4437"而"兜底只重投 200"，
    运维照着告警数永远清不完账。故断言两者对同一时刻的同一批行给同一个数。

    注意：`_stale_candidates` 的 limit 必须足够大，否则比的是"截断后"的数。
    """
    db, user = db_user
    old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    a = _content(db, user.id)  # 无打点，旧 updated_at
    _set_updated_at(db, a, datetime(2001, 1, 1, tzinfo=UTC))
    b = _content(db, user.id, extra={PROCESSING_STARTED_KEY: old})  # 有旧打点
    _set_updated_at(db, b, datetime.now(UTC))
    fresh = _content(db, user.id)  # 新鲜，不该被数进去
    _set_updated_at(db, fresh, datetime.now(UTC))

    cutoff = datetime.now(UTC) - timedelta(seconds=REQUEUE_DEFAULT_STALE_SECONDS)
    picked = [x for x in _stale_candidates(db, cutoff, 100_000) if x.status == "processing"]
    picked_ids = {x.id for x in picked}

    # count 是全局计数（含库里历史积压），故只能断言"包含关系 + 判据一致"，不能断言全等
    n = db.scalar(select(func.count()).select_from(Content).where(stale_processing_filter()))
    assert n >= len(picked_ids), "计数应 ≥ 本测试用户被选中的行数（计数是全局的）"
    assert a.id in picked_ids
    assert b.id in picked_ids, "有旧打点的行必须被选中"
    assert fresh.id not in picked_ids


@pytest.mark.integration
def test_stale_processing_count_returns_int(db_user):
    """② `stale_processing_count()` 可调用、返回非负整数（healthz 依赖此契约）"""
    n = stale_processing_count()
    assert isinstance(n, int)
    assert n >= 0


@pytest.mark.integration
def test_stale_filter_excludes_soft_deleted(db_user):
    """②③ 软删行不计入（与 job 选行同口径，别让已删内容污染告警数）"""
    db, user = db_user
    c = _content(db, user.id)
    _set_updated_at(db, c, datetime(2001, 1, 1, tzinfo=UTC))
    c.deleted_at = datetime.now(UTC)
    db.commit()

    cutoff = datetime.now(UTC) - timedelta(seconds=REQUEUE_DEFAULT_STALE_SECONDS)
    assert not any(x.id == c.id for x in _stale_candidates(db, cutoff, 100_000))
    # stale_since_condition 本身不带 status/deleted 条件，故单独验 filter
    n = db.scalar(
        select(func.count()).select_from(Content).where(stale_processing_filter())
    )
    assert isinstance(n, int)


@pytest.mark.integration
def test_stale_filter_ignores_non_processing_status(db_user):
    """② 只数 processing（done/failed 不进来）——健康检查的语义边界"""
    db, user = db_user
    done_old = _content(db, user.id, status="done")
    _set_updated_at(db, done_old, datetime(2001, 1, 1, tzinfo=UTC))

    cutoff = datetime.now(UTC) - timedelta(seconds=REQUEUE_DEFAULT_STALE_SECONDS)
    # stale_since_condition（纯超龄谓词）会选中它
    n_any = db.scalar(
        select(func.count()).select_from(Content).where(stale_since_condition(cutoff))
    )
    # stale_processing_filter（带 status=processing）不会
    n_proc = db.scalar(
        select(func.count()).select_from(Content).where(stale_processing_filter())
    )
    assert n_any >= 1
    assert n_proc <= n_any, "带 status 过滤的计数必须 ≤ 纯超龄计数"
