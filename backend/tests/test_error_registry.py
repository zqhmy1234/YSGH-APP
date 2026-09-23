"""错误码登记表专项测试（H3 · 由 test_techdebt_p0.py 按域拆分而来，2026-08-27）

覆盖（原 P0-7）：
  - 登记表码唯一（同码多义拆分的根因约束）
  - http 语义匹配：retryable 仅限 5xx；4xx 不可重试；消息非空
  - raise 处码全部在表内（AST 扫描，防新码漏登记/撞号）
  - 已知拆分回归：CONTENT_003/008/007/EVENT_005
  - **门禁自校验**（D11-2）：反向探针证明"就地定义未登记"的码真能被抓到
  - **反向棘轮**（D11-5）：登记但**从不 raise** 的码不得新增（基线冻结 3 枚存量，只允许收缩）

D11-2（2026-09-24 重构波 B10-b）：修跨模块盲区
  旧实现只解析 `app.core.errors` 的 `ERR_*` 常量——**就地定义在他模块**的错误码
  （如 `app/api/capsules.py` 的 `ERR_CAPSULE_001..004`）在 `getattr(E, ...)` 处取不到，
  被**静默丢弃**，4 枚漏登记（缺陷 D11-1，P0）而门禁全绿。
  现改为两段式：
    ① 逐模块解析**本模块**的字符串常量（就地定义**优先**），再回落共享登记表模块；
    ② 收集两类"被使用"的码——(a) `ApiError(<码>)` 首参（str 字面量或 ERR_* 名）
       (b) 任意 `ERR_*` 标识符的**读引用**（覆盖经 helper 间接 raise 的形态，
       如 capsules 把 `ERR_CAPSULE_002` 传给 `deps.load_owned_entity`）。
"""
import ast
import re
from pathlib import Path

import pytest

# H3/R8#10：AST 扫描 + 登记表语义纯单测（无外部依赖）→ unit 分层
pytestmark = pytest.mark.unit

BACKEND_APP = Path(__file__).resolve().parent.parent / "app"

_ERR_NAME_RE = re.compile(r"^ERR_[A-Z0-9_]+$")


def _scan_source_codes(src: str, filename: str = "<memory>") -> set[str]:
    """纯函数：从**一段源码**提取"被使用"的错误码集合（可单测 / 供反向探针）。

    解析规则（见模块 docstring D11-2）：
      · 本模块 module 级字符串常量（`ERR_X = "CODE"`，含 `AnnAssign`）→ 就地常量表；
      · `ApiError(a0, ...)`：a0 为 str 字面量 → 直接取；a0 为 `ERR_*` 名 → 就地表**优先**、否则取共享表；
      · 任意 `ERR_*` 名（Load 上下文，排除定义处的 Store）→ 同上解析（覆盖间接 raise 形态）。
    """
    import app.core.errors as E

    tree = ast.parse(src, filename=filename)
    local: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for tgt in targets:
                if (
                    isinstance(tgt, ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    local[tgt.id] = node.value.value

    codes: set[str] = set()

    def _resolve(name: str) -> None:
        val = local.get(name) or getattr(E, name, None)
        if isinstance(val, str):
            codes.add(val)

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ApiError"
            and node.args
        ):
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                codes.add(a0.value)
            elif isinstance(a0, ast.Name) and _ERR_NAME_RE.match(a0.id):
                _resolve(a0.id)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and _ERR_NAME_RE.match(node.id):
            _resolve(node.id)
    return codes


def _raise_site_codes() -> set[str]:
    """AST 扫描 backend/app 全部模块里"被使用"的错误码（直接/间接 raise + 常量读引用）"""
    codes: set[str] = set()
    for py in BACKEND_APP.rglob("*.py"):
        codes |= _scan_source_codes(py.read_text(encoding="utf-8"), str(py))
    return codes


def test_error_registry_codes_unique():
    """P0-7：登记表码唯一（同码多义拆分的根因约束）"""
    from app.core.errors import ERROR_REGISTRY

    specs = list(ERROR_REGISTRY.values())
    assert len({s.code for s in specs}) == len(specs)


def test_error_registry_http_semantics():
    """P0-7：http 语义匹配——retryable 仅限 5xx；4xx 不可重试；消息非空"""
    from app.core.errors import ERROR_REGISTRY

    for spec in ERROR_REGISTRY.values():
        assert spec.message, f"{spec.code} 缺少语义描述"
        if spec.retryable:
            assert spec.http >= 500, f"{spec.code} retryable=True 但 http={spec.http}（仅 5xx 可重试）"
        if 400 <= spec.http < 500:
            assert not spec.retryable, f"{spec.code} 4xx 不应标记 retryable"


def test_error_registry_covers_all_raise_sites():
    """P0-7：全仓 raise 处使用的码必须已登记（防新码漏登记/撞号）"""
    from app.core.errors import ERROR_REGISTRY

    used = _raise_site_codes()
    missing = used - set(ERROR_REGISTRY)
    assert not missing, f"raise 处存在未登记错误码: {sorted(missing)}"


def test_error_registry_known_splits():
    """P0-7 拆分回归：CONTENT_003 仅敏感语义、CONTENT_008 游标、EVENT_005 内容不存在"""
    from app.core.errors import ERROR_REGISTRY

    assert ERROR_REGISTRY["CONTENT_003"].http == 422
    assert ERROR_REGISTRY["CONTENT_008"].http == 422
    assert ERROR_REGISTRY["EVENT_005"].http == 404
    assert ERROR_REGISTRY["CONTENT_007"].http == 413  # 413 语义不再被 404 污染


# ─────────────────── D11-2 门禁自校验（反向探针）───────────────────
#
# 为什么必须自校验：D11-1 的成因不是"漏登记"，而是**门禁不报**——就地定义在他模块
# 的码被静默丢弃。若只补登记而不锁住"能抓到"的能力，下一次同样会再次全绿放过。

_LOCAL_CODE_PROBE = """
from app.core.errors import ApiError

ERR_LOCAL_999 = "LOCAL_999"


def _boom():
    raise ApiError(ERR_LOCAL_999, "就地定义未登记探针", http=422)
"""


def test_error_registry_gate_detects_unregistered_local_code():
    """自校验：门禁纯函数必须抓到"就地定义未登记"的码（D11-1 反向探针）。

    复现 D11-1 的确切形态：`ERR_*` 常量**定义并使用于同一非 errors 模块**。
    若 `_scan_source_codes` 退化为只认共享表常量，本测试即红。
    """
    from app.core.errors import ERROR_REGISTRY

    used = _scan_source_codes(_LOCAL_CODE_PROBE, "<probe-local>")
    assert "LOCAL_999" in used, "门禁未能解析模块内就地常量 → D11-1 跨模块盲区复发"
    assert "LOCAL_999" not in ERROR_REGISTRY, "探针码不得已登记（否则无法证明门禁会红）"
    assert used - set(ERROR_REGISTRY), "未登记码必须能被门禁判为缺口"


def test_error_registry_gate_detects_naked_literal():
    """自校验补强：`raise ApiError("CAPSULE_001", ...)` 裸字面量形态同样必须被收。"""
    from app.core.errors import ERROR_REGISTRY

    used = _scan_source_codes('raise ApiError("LOCAL_998", "裸字面量探针")', "<probe-literal>")
    assert "LOCAL_998" in used
    assert "LOCAL_998" not in ERROR_REGISTRY


def test_error_registry_gate_detects_indirect_code():
    """自校验补强：经 helper **间接** raise 的码也必须被收。

    复现 capsules 的 `ERR_CAPSULE_002` 形态——码作为实参传给 `deps.load_owned_entity`，
    由该 helper 内部 `raise ApiError(error_code, ...)`；只认 `ApiError(...)` 首参的旧实现
    对此完全失明。
    """
    src = """
from app.api.deps import load_owned_entity

ERR_LOCAL_997 = "LOCAL_997"


def f(db, model, i, u):
    return load_owned_entity(db, model, i, u, ERR_LOCAL_997, "探针")
"""
    used = _scan_source_codes(src, "<probe-indirect>")
    assert "LOCAL_997" in used, "门禁未能覆盖经 helper 间接 raise 的码"


def test_error_registry_capsule_codes_registered():
    """D11-2 闭环：capsule 域 4 枚就地错误码已补登记，且 http 与 raise 点**逐条一致**。

    capsules.py 为显式带 http（001=422 / 002=404 / 003=409 / 004=409），
    登记错 http 会静默改变实际响应码，故逐条断言。
    """
    from app.core.errors import ERROR_REGISTRY

    assert ERROR_REGISTRY["CAPSULE_001"].http == 422
    assert ERROR_REGISTRY["CAPSULE_002"].http == 404
    assert ERROR_REGISTRY["CAPSULE_003"].http == 409
    assert ERROR_REGISTRY["CAPSULE_004"].http == 409


# ─────────────── D11-5 反向棘轮：注册但从不 raise（死登记）───────────────
#
# 为什么需要：上面的正向门禁只保证「raise 的码已登记」，对**反向**完全失明——
# 一个码被登记却没有任何 raise 点（如 `AUTH_099` 在 R4#11 拆分后成为残留、
# `CONTENT_004` / `CONTENT_011` 亦无 raise），会永久占位，使「登记表 = 可发生
# 错误全集」这一承诺失真，并诱导运维/客户端按**不存在的码**写分支。
#
# 棘轮口径（同 B1）：**基线冻结存量、只拦新增**，且基线**只允许收缩**；
# 每项必须带原因；真正删除该码或补上 raise 点后，必须从基线移除。

DEAD_CODE_BASELINE: dict[str, str] = {
    "AUTH_099": (
        "R4#11（2026-08-27）把通用「认证服务未接入或上游不可用」拆为 AUTH_010/011/012 后"
        "未删除的原码 → 纯残留，可直接删（销项触发：确认无外部消费方即删）"
    ),
    "CONTENT_004": (
        "「STS 直传未接入（生产待实现）」501 占位；STS 直传链路已落地 → 无 raise 点"
        "（销项触发：确认无外部消费方即删）"
    ),
    "CONTENT_011": (
        "「收藏状态冲突（重复收藏/未收藏）」409 占位；收藏端点走幂等语义 → 无 raise 点"
        "（销项触发：确认无外部消费方即删）"
    ),
}


def _dead_registered_codes(registered, used) -> set[str]:
    """纯函数：**已登记但从不被 raise** 的码集合（供断言与自校验复用）"""
    return set(registered) - set(used)


def test_no_new_dead_error_codes():
    """反向棘轮（D11-5）：不得新增「注册但从不 raise」的码；基线只允许收缩

    双向判定：
      · 出现基线外的新死登记 → 红（要求删登记或补真实 raise 点）；
      · 基线项已不再是死登记 → 也红（要求从基线移除，防僵尸豁免）。
    """
    from app.core.errors import ERROR_REGISTRY

    dead = _dead_registered_codes(ERROR_REGISTRY, _raise_site_codes())
    new_dead = sorted(dead - set(DEAD_CODE_BASELINE))
    assert not new_dead, (
        "新增了「已登记但从不被 raise」的错误码（登记表 → 失真）：\n"
        + "\n".join(f"  {c}" for c in new_dead)
        + "\n\n修法：删掉该登记，或补上真实 raise 点。"
    )
    shrunk = sorted(set(DEAD_CODE_BASELINE) - dead)
    assert not shrunk, (
        "以下 DEAD_CODE_BASELINE 项已不再是死登记（已删除或已补 raise），"
        "请从基线移除（棘轮只允许收缩，禁止留僵尸豁免）：\n"
        + "\n".join(f"  {c}" for c in shrunk)
    )


def test_dead_code_ratchet_selfcheck():
    """自校验：反向棘轮的判定函数必须双向可用（防"永远绿"的空转）"""
    assert _dead_registered_codes({"A", "B"}, {"A"}) == {"B"}, "未识别出死登记"
    assert _dead_registered_codes({"A"}, {"A", "B"}) == set(), "误报死登记"
