"""Agent 记忆镜像端点（W1 记忆域打通 · 2026-10-06）

见 docs/功能对齐计划_20261006.md W1。AI 对话页保存的记忆此前只进 agent 自己的
memories 表，App 的时间轴/检索/回响完全看不见——「用户以为记住了，其实没有」。
这两个端点让 agent 以服务间身份把记忆镜像成正式 contents（进管线 → 向量化 →
时间轴/检索/回响全通）。

鉴权走 `agent_service_user`（与 `/api/v1/search/agent` 同一把共享密钥、同一条
fail-closed 语义：token 未配置 ⇒ 503，错 token ⇒ 401，代执行用户须真实存在）。

⚠️ 本模块**导入时**把端点注册到共享 router（`app.api.contents.serializers.router`），
由 `app/api/contents/__init__.py` 在**末尾**导入——与 favorites/trash/waveform
同款拆包模式，保证路由注册顺序。
"""
from datetime import UTC, datetime, timedelta

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.contents.serializers import router
from app.api.deps import agent_service_user, find_alive_content_by_cgid
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.content import (
    TRASH_RETENTION_DAYS,
    ContentCreate,
    ContentDeleteOut,
    ContentOut,
)
from app.services import sync_writes


@router.post("/agent", response_model=ApiResponse[ContentOut], include_in_schema=False)
def create_content_for_agent(
    req: ContentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(agent_service_user),
):
    """agent 记忆镜像写入：**直接复用** `create_content` 的完整函数体。

    为什么是"调用路由函数"而不是抽 service 层重写：`create_content` 里挂着
    四件**不能绕过**的事——①护栏检测（reject 拒收/mask 打码，SAF-005 在镜像
    路径同样生效）、②client_generated_id 幂等、③变更日志（他端 pull 有源）、
    ④RQ 管线入队（向量化→可检索）。任何"照着写一份"的实现都会在某次改动后
    悄悄漏掉其中一件。函数本身是普通函数（`user` 只被取 `.id`），
    `agent_service_user` 返回的又是同一类型，直接传参调用即可——行为与 App
    写入**逐字节一致**，零逻辑复制。若未来 `create_content` 增加 `Request`
    之类不可直传的依赖，这里会立即报错（好事：强制重构，而不是静默绕过）。

    延迟量级：请求路径内只有一次护栏 `moderate()`（秒级）；分类/打标/Qdrant
    索引全在 low 队列 worker 异步做——保存即返回，检索稍后可见（与 App 自身
    创建语义一致）。
    """
    from app.api.contents import create_content  # 局部 import：避免与 __init__ 循环

    return create_content(req, db=db, user=user)


@router.delete(
    "/agent/by-cgid/{client_generated_id}",
    response_model=ApiResponse[ContentDeleteOut],
    include_in_schema=False,
)
def delete_content_for_agent_by_cgid(
    client_generated_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(agent_service_user),
):
    """agent 记忆镜像删除：按 client_generated_id 软删（幂等）。

    agent 删除自己的记忆时须同步删镜像，否则出现「AI 页删了、App 还在」的
    分叉。走**同一条软删路径** `sync_writes.soft_delete_content`（四件事一次做齐：
    权威表删除位 + SFV 墓碑 + deleted_logs + 变更日志），与 App 删除语义完全一致。
    **幂等**：找不到（未镜像过/已删）返回 `deleted=False` 而不是 404——
    agent 侧删除不该因为"镜像本就不存在"而失败。
    """
    row = find_alive_content_by_cgid(db, user.id, client_generated_id)
    now = datetime.now(UTC)
    if row is None:
        return ApiResponse(data=ContentDeleteOut(content_id="", deleted=False, permanent_at=now))
    sync_writes.soft_delete_content(db, row, user.id, change_ts=now, anchor=now)
    db.commit()
    return ApiResponse(
        data=ContentDeleteOut(
            content_id=row.id,
            deleted=True,
            permanent_at=now + timedelta(days=TRASH_RETENTION_DAYS),
        )
    )
