"""画像枚举集**分发**回归（D07-1 · 功能修复波 域⑦ · P0）。

缺陷原貌：`services/profile_schema.py` 从**仓库根 `docs/`** 读两份枚举集 JSON，而
`deploy/Dockerfile.backend` 只 `COPY backend/ ./` ⇒ 容器内首次 `get_schema()` 抛
`FileNotFoundError` ⇒ **画像页 / 访谈 / 画像标注首调 500**（宿主侧开发完全看不出）。

修复：Dockerfile 随镜像分发两份 JSON 并用 `PROFILE_ENUM_DIR` 指向；缺失时报**可执行**
的错误信息；启动期 fail-fast（`main.lifespan` 调 `get_schema()`）。

本文件同时锁三件事：
  ① 仓库内两份 JSON 真实存在且可被加载（`validate()` 维度数符合契约）；
  ② 加载器**真的**认 `PROFILE_ENUM_DIR`（= 镜像里的分发机制本身）；
  ③ Dockerfile 仍包含该 COPY 与 ENV（源码级防漂移，删掉即红）。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from app.services import profile_schema as ps

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
DOCKERFILE = REPO_ROOT / "deploy" / "Dockerfile.backend"


@pytest.fixture(autouse=True)
def _clear_schema_cache():
    """get_schema 是 lru_cache 单例：用例前后清缓存，避免 ENV 切换互相污染。"""
    ps.get_schema.cache_clear()
    yield
    ps.get_schema.cache_clear()


def test_enum_json_present_and_loadable():
    """① 仓库侧：两份枚举集齐备且能构建出通过校验的 schema。"""
    for name in (ps.L0_FILENAME, ps.L1_FILENAME):
        path = DOCS_DIR / name
        assert path.exists(), f"枚举集缺失：{path}"
        assert path.stat().st_size > 0

    schema = ps.get_schema()
    assert schema.l0_count == 51
    assert schema.l1_count == 193
    assert schema.validate() == [], f"枚举集契约校验未通过: {schema.validate()}"


def test_loader_honors_profile_enum_dir(tmp_path, monkeypatch):
    """② 分发机制：把两份 JSON 放到任意目录 + `PROFILE_ENUM_DIR` 指向它即可加载。

    这正是镜像内的部署形态（`/app/profile_enums`）——若加载器不认该变量，
    Dockerfile 的 COPY 就白做了。
    """
    for name in (ps.L0_FILENAME, ps.L1_FILENAME):
        shutil.copy(DOCS_DIR / name, tmp_path / name)

    monkeypatch.setenv("PROFILE_ENUM_DIR", str(tmp_path))
    ps.get_schema.cache_clear()
    schema = ps.get_schema()
    assert schema.l0_count == 51
    assert ps._enum_dir() == tmp_path


def test_missing_enum_dir_gives_actionable_error(tmp_path, monkeypatch):
    """缺失时错误必须**可执行**（点名缺哪份、查哪个目录、该配哪个变量）。"""
    monkeypatch.setenv("PROFILE_ENUM_DIR", str(tmp_path / "not-there"))
    ps.get_schema.cache_clear()
    with pytest.raises(RuntimeError) as exc:
        ps.get_schema()
    msg = str(exc.value)
    assert ps.L0_FILENAME in msg and ps.L1_FILENAME in msg
    assert "PROFILE_ENUM_DIR" in msg


def test_dockerfile_ships_profile_enums_and_env():
    """③ 防漂移：Dockerfile 必须随镜像分发两份枚举集并声明 PROFILE_ENUM_DIR。

    反向保护——若有人删掉 COPY/ENV（或改错目标目录），本用例即红，
    避免"宿主侧全绿、部署后画像 500"再次复发。
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    copy_line = next(
        (ln for ln in text.splitlines() if ln.startswith("COPY") and ps.L0_FILENAME in ln),
        None,
    )
    assert copy_line is not None, "Dockerfile 缺枚举集 COPY 指令（D07-1 会复发）"
    assert ps.L1_FILENAME in copy_line, "COPY 必须同时带上 L1 骨架（缺一件即启动失败）"
    assert "/app/profile_enums" in copy_line, "分发目标目录应与 PROFILE_ENUM_DIR 一致"

    env_line = next(
        (ln for ln in text.splitlines() if ln.startswith("ENV") and "PROFILE_ENUM_DIR" in ln),
        None,
    )
    assert env_line is not None, "Dockerfile 未声明 PROFILE_ENUM_DIR"
    assert "/app/profile_enums" in env_line


def test_app_startup_loads_schema():
    """启动自检真的被接到 lifespan 上（否则 fail-fast 只是文档承诺）。"""
    import inspect

    from app import main as main_mod

    src = inspect.getsource(main_mod.lifespan)
    assert "get_schema" in src, "lifespan 未做枚举集启动自检"
