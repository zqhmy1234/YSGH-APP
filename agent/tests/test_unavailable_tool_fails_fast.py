"""必然失败的工具必须在**入口**诚实失败（2026-10-05）。

## 为什么值得单独一个测试文件

本仓吃到过最大的一次性能事故，根因就是「工具失败但 LLM 不知道」：
`save_memory` 因 SQL 报错静默返回空列表 → 模型以为没存上 → 单轮重调 16~28 次
→ 输入 token 涨到 13K → 32~66s 后撞递归上限。

`export_knowledge_collections` 是同一根因族的**另一个样本**，而且更彻底：
它依赖 Coze 平台的私有包（`coze_coding_dev_sdk`）与环境变量，两者在去 Coze 后
都不存在 ⇒ **每次调用必然失败**。偏偏它同时被写进工具列表**和** system prompt，
所以用户一提"导出收藏"就会被调用。

修法不是"把异常吞掉"，而是**在入口就给出确定结论**：
返回一个 `success=False` 的 JSON，模型据此直接如实转述，不再重试。

覆盖：
1. 缺依赖时入口即返回，**不触碰 DB**（不查库就不会有副作用与额外延迟）
2. 返回体不含内部模块路径/异常原文（与 /v1/chat 的信息泄漏口径一致）
3. 返回体明确要求模型**不要重试**（这是打断循环的关键指令）
4. 依赖与配置齐备时预检放行（不能把正常路径一并打死）
5. 内部异常不再以 `str(e)` 形式进入 LLM 上下文
"""
from __future__ import annotations

import json

import pytest
from tools import url_fetch_tools as uft


@pytest.fixture
def no_coze_sdk(monkeypatch):
    """模拟当前真实环境：Coze 私有包未安装、COZE_BUCKET_* 未配置。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("coze_coding_dev_sdk"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delenv("COZE_BUCKET_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("COZE_BUCKET_NAME", raising=False)


def test_export_fails_fast_at_entry_without_touching_db(no_coze_sdk, monkeypatch):
    """缺依赖 ⇒ 入口返回，**不得**触发任何 DB 查询。

    为什么这条很重要：如果先查库再判断，每次调用都要先付出一次数据库往返 +
    （更糟）在用户没有任何收藏时返回"没有可导出的内容"——那是个**假成功**，
    用户会以为导出只是内容为空，而真实原因是功能压根没接上。
    """
    def must_not_be_called():
        raise AssertionError("预检未生效：仍然初始化了 DB 客户端")

    monkeypatch.setattr(uft, "_get_client", must_not_be_called)
    monkeypatch.setattr(uft, "_get_user_id", must_not_be_called)

    payload = json.loads(uft.export_knowledge_collections.invoke({}))

    assert payload["success"] is False
    assert payload["unavailable"] is True


def test_export_message_does_not_leak_internals(no_coze_sdk):
    """返回体会进 LLM 上下文 ⇒ 不得含模块路径、绝对路径、异常原文。

    与 `/v1/chat` 去掉 `str(exc)` 是同一口径：异常原文只进日志。
    ⚠️ 这里**曾有一个反向断言**（要求 message 里出现 `coze_coding_dev_sdk`），
    那是把"内部包名可以进 LLM"当成了可操作信息——2026-10-06 修正：
    包名对模型毫无价值且属内部信息外泄，运维细节只走日志。
    """
    payload = json.loads(uft.export_knowledge_collections.invoke({}))
    message = payload["message"]
    assert "No module named" not in message
    assert "Traceback" not in message
    assert "C:\\" not in message and "/usr/" not in message
    # 内部包名**不得**出现在会进 LLM 上下文的返回体里
    assert "coze_coding_dev_sdk" not in message
    # 但要让模型知道这是"能力暂不可用"，能如实转述
    assert "不可用" in message or "不支持" in message


def test_export_message_tells_model_not_to_retry(no_coze_sdk):
    """**打断重试循环的关键**：明确告知模型不要重试、不要报成"失败"。

    只说"不可用"不够——模型仍可能换个参数再试一次。必须显式禁止。
    """
    payload = json.loads(uft.export_knowledge_collections.invoke({}))
    assert "不要重试" in payload["message"]
    # 也要引导它给用户一个准确的说明，而不是含糊的"出错了"
    assert "收藏" in payload["message"]


def test_reason_prefers_missing_dependency_over_missing_config(monkeypatch):
    """缺依赖 + 缺配置同时存在时，报"缺依赖"更有指向性（配了凭据也用不了）。

    同时断言：reason **不含内部包名**（它会进 LLM 上下文）。
    """
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("coze_coding_dev_sdk"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delenv("COZE_BUCKET_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("COZE_BUCKET_NAME", raising=False)
    reason = uft._export_unavailable_reason()
    assert "依赖" in reason or "存储" in reason
    assert "coze_coding_dev_sdk" not in reason


def test_env_missing_but_dependency_present_reports_config(monkeypatch):
    """依赖在、配置缺 ⇒ 报配置问题（下一步该做什么很清楚），同样不带内部包名。

    本用例必须**真的把假模块注入 sys.modules**——否则在本机（SDK 未安装）上
    预检会先命中"缺依赖"分支，走到的是另一条文案，用例就变成了
    "测的名字"与"测的东西"不一致（实测踩过：没注入假模块，本地恒红）。
    """
    import sys
    import types

    pkg = types.ModuleType("coze_coding_dev_sdk")
    sub = types.ModuleType("coze_coding_dev_sdk.s3")
    pkg.s3 = sub
    monkeypatch.setitem(sys.modules, "coze_coding_dev_sdk", pkg)
    monkeypatch.setitem(sys.modules, "coze_coding_dev_sdk.s3", sub)
    monkeypatch.delenv("COZE_BUCKET_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("COZE_BUCKET_NAME", raising=False)
    reason = uft._export_unavailable_reason()
    assert "对象存储" in reason
    assert "coze_coding_dev_sdk" not in reason


def test_preflight_passes_when_everything_configured(monkeypatch):
    """依赖与配置齐备时必须放行——预检不能把正常路径一起打死。

    用假的 `coze_coding_dev_sdk.s3` 模块模拟"装上且配好"的形态。
    """
    import sys
    import types

    pkg = types.ModuleType("coze_coding_dev_sdk")
    sub = types.ModuleType("coze_coding_dev_sdk.s3")
    pkg.s3 = sub
    monkeypatch.setitem(sys.modules, "coze_coding_dev_sdk", pkg)
    monkeypatch.setitem(sys.modules, "coze_coding_dev_sdk.s3", sub)
    monkeypatch.setenv("COZE_BUCKET_ENDPOINT_URL", "https://bucket.example")
    monkeypatch.setenv("COZE_BUCKET_NAME", "demo")
    assert uft._export_unavailable_reason() == ""


def test_internal_exception_does_not_leak_str_exc(monkeypatch):
    """依赖齐备但执行期失败时，抛出的 message 不得带异常原文。"""
    import sys
    import types

    pkg = types.ModuleType("coze_coding_dev_sdk")
    sub = types.ModuleType("coze_coding_dev_sdk.s3")

    class _Boom:
        def __init__(self, **_kw):
            raise RuntimeError(
                "postgresql+psycopg://user:hunter2@10.0.0.5:5432/yishu 连接失败"
            )

    sub.S3SyncStorage = _Boom
    pkg.s3 = sub
    monkeypatch.setitem(sys.modules, "coze_coding_dev_sdk", pkg)
    monkeypatch.setitem(sys.modules, "coze_coding_dev_sdk.s3", sub)
    monkeypatch.setenv("COZE_BUCKET_ENDPOINT_URL", "https://bucket.example")
    monkeypatch.setenv("COZE_BUCKET_NAME", "demo")

    class _Resp:
        data = [{"id": 1, "content": "x", "title": "t"}]

    class _Q:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def order(self, *_a, **_k):
            return self

        def execute(self):
            return _Resp()

    def _make_query(*_a, **_k):
        return _Q()

    fake_client = type("C", (), {"table": _make_query})()
    monkeypatch.setattr(uft, "_get_client", lambda: fake_client)
    monkeypatch.setattr(uft, "_get_user_id", lambda: "u1")

    with pytest.raises(Exception) as excinfo:
        uft.export_knowledge_collections.invoke({})
    text = str(excinfo.value)
    assert "hunter2" not in text
    assert "://" not in text
    assert "RuntimeError" in text  # 仍保留可区分的类型名
