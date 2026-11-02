"""C 批音频收录脚本单测（scripts/sync_c_audio.py）

只测两个纯函数：真容器嗅探、编号归一。它们决定落进 `research/truth-data/c/audio/`
的文件叫什么名 —— 错了会让 `convert_truth_xlsx.py` 找不到音频（MISSING_MEDIA_FILE）。

真实素材在仓库外（`<工作区>/真值数据/素材/`），CI 拿不到，所以这里现造魔数。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (_ROOT, _ROOT / "backend", _ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import scripts.sync_c_audio as sync  # noqa: E402

pytestmark = pytest.mark.unit


def test_sniff_container_ignores_wrong_extension(tmp_path):
    """素材里 `a001.mp3` 其实是 M4A —— 扩展名不可信，只认文件头。"""
    m4a = tmp_path / "a001.mp3"
    m4a.write_bytes(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 16)
    mp3 = tmp_path / "b.mp3"
    mp3.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00\xff\xfb\x90")
    wav = tmp_path / "c.wav"
    wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt \x10\x00")

    assert sync.sniff_container(m4a) == ".m4a"
    assert sync.sniff_container(mp3) == ".mp3"
    assert sync.sniff_container(wav) == ".wav"

    junk = tmp_path / "d.mp3"
    junk.write_bytes(b"not audio at all")
    assert sync.sniff_container(junk) is None


@pytest.mark.parametrize(("name", "expected"), [
    ("a001.mp3", ("a", "0001")),
    ("a-0006.mp3", ("a", "0006")),
    ("d-0009.mp3", ("d", "0009")),
    ("D1.mp3", ("d", "0001")),
    ("c批片段1.mp3", None),
    ("测试音频.mp3", None),
])
def test_canonical_name(name, expected):
    """编号归一：`a001` 与 `a-0006` 都要落成 `组字母-4位序号`。"""
    assert sync.canonical_name(Path(name)) == expected


def test_build_manifest_records_extension_correction(tmp_path):
    """清单要写清「为什么改名」，否则半年后没人知道 a-0001.m4a 是从哪来的。"""
    src_root = tmp_path / "素材"
    dst = tmp_path / "audio"
    (src_root / "组").mkdir(parents=True)
    dst.mkdir()
    (src_root / "组" / "a001.mp3").write_bytes(b"x")
    target = dst / "a-0001.m4a"
    target.write_bytes(b"abc")

    manifest = sync.build_manifest(src_root, dst, {"a-0001.m4a": src_root / "组" / "a001.mp3"})

    assert manifest == [{
        "dst": "research/truth-data/c/audio/a-0001.m4a",
        "src": "组/a001.mp3",
        "bytes": 3,
        "container": ".m4a",
        "note": "扩展名更正：.mp3 → .m4a（按文件头嗅探）",
    }]
