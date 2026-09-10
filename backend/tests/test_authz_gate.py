"""归属校验 AST 静态门禁（波D ③ · 2026-09-10）

## 它解决什么

「按 id 读取/变更既有实体」的端点若漏写归属校验，就是 IDOR（水平越权）——
A 用户凭 B 的资源 id 读到/改到 B 的数据。人工 review 抓不全（波D 侦察实录：
用正则扫「带 id 参数的端点是否引用 user.id」得 4 命中**全误报**——创建型端点
本无归属概念、而 `content.user_id != user.id` 这种写法没被模式认出来）。

本门禁改用 **AST 精确解析**：只认函数体里真实的归属语义，不认文本近似。

## 判定规格

候选 = `@<router>.(get|patch|put|delete|post)` 且（路径含 `{*_id}` 或形参含 `*_id`）。
> 说明：规格原文只列 get|patch|put|delete，实测**操作型 POST 大量存在**
> （`POST /messages/{id}/read`、`POST /capsules/{id}/open`、`POST /contents/{id}/favorite`、
> `POST /trash/{id}/restore`、`POST /wechat/delete`…），只覆盖四方法等于漏掉半个面，
> 故本门禁纳入 POST 并以白名单排除「真·创建型」。

候选须满足其一，否则测试失败：
  1. 函数体调用了 ② 建立的统一 loader（OWNERSHIP_LOADERS）
  2. 函数体出现归属语义标识：`user.id` 属性访问 / `user_id` 标识符或关键字实参
  3. 登记在 EXEMPT（文件+函数+理由三列，逐条人工核对，**禁止通配兜底**）

白名单（CREATION_WHITELIST）与 EXEMPT 都必须带行内理由——这是防「加个通配就把门禁
焊死成摆设」的最后一道闸。新端点若两者都不满足 → CI 红。

## 维护

新增「操作既有实体」端点时：走 loader 或显式引用 user.id 即可自动通过；
确属创建型/无归属概念的，补进 CREATION_WHITELIST 并写明理由。
"""
import ast
import pathlib

import pytest

pytestmark = pytest.mark.unit

API_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "api"

# 被门禁认可的「取本人既有实体」统一 loader（② 收敛产物 + 各域既有 loader）
OWNERSHIP_LOADERS = frozenset(
    {
        "load_alive_content",      # deps.py（波D ② 提升；原 contents._load_alive_content）
        "load_owned_content",      # 预留别名
        "load_owned_capsule",      # capsules.py（既有，域内）
        "load_owned_message",      # messages.py（波D ② 新建）
        "load_owned_event",        # 预留（events 域现状由 services/events/edit._get_event 承担）
        "load_owned_task",         # 预留（upload 域现状由 services/upload/protocol 承担）
    }
)

# 候选识别方法（规格四方法 + 操作型 POST）
ID_METHODS = frozenset({"get", "patch", "put", "delete", "post"})

# ---------------------------------------------------------------------------
# 白名单：非「按 id 操作既有实体」语义，逐条人工核对源码后登记
# ---------------------------------------------------------------------------
CREATION_WHITELIST = {
    ("contents.py", "list_contents"): (
        "列表型：content_id 是**可选 query 过滤条件**（列表筛选），不是「取单个既有实体」；"
        "查询自带 user_id + deleted_at 过滤（L403-409）"
    ),
    ("upload.py", "init_upload"): (
        "创建型：client_upload_id 是幂等键，本端点**新建**上传任务，无既有归属可读"
    ),
    ("sync.py", "sync_pull"): (
        "device_id 是**设备标识**而非既有实体 id；数据按 user_id + since 游标过滤"
    ),
}

# ---------------------------------------------------------------------------
# 残余例外：文件 + 函数 + 理由（禁止通配兜底）
# ---------------------------------------------------------------------------
EXEMPT = {
    ("upload.py", "upload_chunk"): (
        "薄壳：函数体直接委托 _upload_chunk_impl(upload_id, ..., db, user)，"
        "后者以 user_id=user.id 下传 service（services/upload/protocol.upload_chunk）做归属校验"
    ),
    ("upload.py", "upload_chunk_post"): (
        "同上：POST 别名（uni.uploadFile 不支持 PUT），与 PUT /chunk 共用同一 _upload_chunk_impl"
    ),
}


def _router_var_names(tree: ast.Module) -> set[str]:
    """收集模块内由 make_router(...) 赋值的 router 变量名（含多 router 模块）"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if isinstance(fn, ast.Name) and fn.id == "make_router":
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        names.add(tgt.id)
    return names


def _iter_id_candidates(module_path: pathlib.Path):
    """产出该模块内所有「按 id 操作既有实体」候选：(func_node, method, path)"""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    routers = _router_var_names(tree)
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            base = dec.func.value
            if not (isinstance(base, ast.Name) and base.id in routers):
                continue
            method = dec.func.attr
            if method not in ID_METHODS:
                continue
            path = ""
            if dec.args and isinstance(dec.args[0], ast.Constant):
                path = str(dec.args[0].value)
            params = [a.arg for a in node.args.args] + [
                a.arg for a in node.args.kwonlyargs
            ]
            id_in_path = "_id}" in path
            id_in_param = any(p.endswith("_id") for p in params)
            if id_in_path or id_in_param:
                yield node, method, path


def _ownership_evidence(func_node) -> str | None:
    """返回函数体内发现的归属语义证据；无则 None

    只认 AST 层面的真实语义（非文本近似）：
      - 调用统一 loader
      - `user.id` 属性访问
      - `user_id` 标识符 / 关键字实参
    """
    for sub in ast.walk(func_node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            if isinstance(fn, ast.Name) and fn.id in OWNERSHIP_LOADERS:
                return f"loader:{fn.id}"
            if isinstance(fn, ast.Attribute) and fn.attr in OWNERSHIP_LOADERS:
                return f"loader:{fn.attr}"
        if (
            isinstance(sub, ast.Attribute)
            and sub.attr == "id"
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "user"
        ):
            return "user.id"
        if isinstance(sub, ast.Name) and sub.id == "user_id":
            return "user_id"
        if isinstance(sub, ast.keyword) and sub.arg == "user_id":
            return "user_id(kwarg)"
    return None


def _all_candidates():
    """全量候选（跨 app/api/*.py），供多个断言复用"""
    out = []
    for module_path in sorted(API_DIR.glob("*.py")):
        for func_node, method, path in _iter_id_candidates(module_path):
            out.append((module_path.name, func_node.name, method, path, func_node))
    return out


def test_candidates_found():
    """前置哨兵：扫描器必须真的扫到端点（防 AST 规则写错后「零候选 = 全绿」的假阴性）"""
    cands = _all_candidates()
    assert len(cands) >= 20, (
        f"候选路由只有 {len(cands)} 个，疑似扫描器失效（AST 规则或 API 目录定位错误）——"
        "门禁若扫不到东西会永远绿，比没有门禁更危险"
    )


def test_every_id_endpoint_has_ownership_check():
    """核心门禁：每个「按 id 操作既有实体」端点都必须做归属校验

    未来任何新端点写漏归属 → 本断言失败 → CI 红。
    """
    violations: list[str] = []
    for module_name, func_name, method, path, func_node in _all_candidates():
        key = (module_name, func_name)
        if key in CREATION_WHITELIST or key in EXEMPT:
            continue
        evidence = _ownership_evidence(func_node)
        if evidence is None:
            violations.append(
                f"  {module_name}::{func_name}  [{method.upper()} {path or '(空路径)'}]\n"
                f"      → 未发现归属校验：既未调用 loader，也未引用 user.id / user_id"
            )
    assert not violations, (
        "以下「按 id 操作既有实体」的端点缺少归属校验（IDOR 风险）:\n"
        + "\n".join(violations)
        + "\n\n修法：改用 app/api/deps.py::load_alive_content 之类统一 loader，"
        "或在查询 where 中显式引用 user.id；确非「取既有实体」语义的，"
        "补进 CREATION_WHITELIST 并写明理由（禁止通配兜底）。"
    )


def test_whitelist_and_exempt_entries_still_exist():
    """防僵尸条目：白名单/EXEMPT 指向的函数若已被删除或改名，门禁必须提醒清理

    不做通配、不做静默——条目失配即为维护信号（防止白名单无限膨胀成摆设）。
    """
    live = {(m, f) for m, f, _method, _path, _node in _all_candidates()}
    stale = [
        f"  {m}::{f}（白名单）" for (m, f) in CREATION_WHITELIST if (m, f) not in live
    ] + [f"  {m}::{f}（EXEMPT）" for (m, f) in EXEMPT if (m, f) not in live]
    assert not stale, (
        "以下白名单/EXEMPT 条目已失效（函数被删/改名/不再匹配 id 候选），请清理:\n"
        + "\n".join(stale)
    )


# ---------------------------------------------------------------------------
# 门禁自测：证明它「抓得住违规」而不是永远绿
# 背景：波D 侦察实录——正则版门禁 4 命中全误报，半成品门禁=负资产。
# 本节用假源码验证扫描器的**双向**判定力，属于门禁的可信度基础。
# ---------------------------------------------------------------------------


def _scan_fake(tmp_path, source: str):
    """把源码写进临时模块，返回扫描结果 [(func_node, method, path), ...]"""
    fake = tmp_path / "fake_api.py"
    fake.write_text(source, encoding="utf-8")
    return list(_iter_id_candidates(fake))


def test_scanner_detects_missing_ownership(tmp_path):
    """① 负例：裸查端点（路径含 {content_id} 但无任何归属语义）必须被判为无证据

    若本用例失败（即 return 非 None），说明扫描器形同虚设——门禁会永远绿。
    """
    cands = _scan_fake(
        tmp_path,
        "from app.api import make_router\n"
        'router = make_router(prefix="/x", tags=["x"])\n'
        "\n"
        '@router.get("/{content_id}")\n'
        "def bare_endpoint(content_id: str, db=None, user=None):\n"
        "    return db.execute('select 1')\n",
    )
    assert len(cands) == 1, f"扫描器应识别出 1 个候选，实得 {len(cands)}"
    assert _ownership_evidence(cands[0][0]) is None, (
        "扫描器未能判定「无归属校验」——门禁失效（永远绿）"
    )


def test_scanner_accepts_both_valid_patterns(tmp_path):
    """② 正例：loader 调用 与 user.id 显式过滤 两种合法写法都必须被认可

    防止门禁把正确的收敛写法误判为违规（误报同样是负资产）。
    """
    via_loader = _scan_fake(
        tmp_path,
        "from app.api import make_router\n"
        "from app.api.deps import load_alive_content\n"
        'router = make_router(prefix="/x", tags=["x"])\n'
        "\n"
        '@router.get("/{content_id}")\n'
        "def ok_endpoint(content_id: str, db=None, user=None):\n"
        "    return load_alive_content(db, user.id, content_id)\n",
    )
    assert len(via_loader) == 1
    assert _ownership_evidence(via_loader[0][0]) == "loader:load_alive_content"

    via_where = _scan_fake(
        tmp_path,
        "from app.api import make_router\n"
        'router = make_router(prefix="/x", tags=["x"])\n'
        "\n"
        '@router.delete("/{event_id}")\n'
        "def ok2_endpoint(event_id: str, db=None, user=None):\n"
        "    return db.execute(select(Event).where(Event.id == event_id,"
        " Event.user_id == user.id))\n",
    )
    assert len(via_where) == 1
    assert _ownership_evidence(via_where[0][0]) == "user.id"


def test_scanner_covers_operational_post(tmp_path):
    """③ 覆盖性：操作型 POST（如 POST /{id}/read）必须纳入候选

    规格原文只列 get|patch|put|delete，会漏掉大量操作型 POST；本门禁显式纳入。
    """
    cands = _scan_fake(
        tmp_path,
        "from app.api import make_router\n"
        'router = make_router(prefix="/x", tags=["x"])\n'
        "\n"
        '@router.post("/{msg_id}/read")\n'
        "def mark_read(msg_id: int, db=None, user=None):\n"
        "    return db.execute('update ...')\n",
    )
    assert len(cands) == 1, "操作型 POST 未被纳入候选——门禁存在覆盖盲区"
    assert cands[0][1] == "post"
