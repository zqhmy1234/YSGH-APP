"""COS + CI 真机冒烟（S5-03/WP-E · 2026-08-19）

流程：上传测试图（COS put_object）→ 读回 SHA256 校验 → CI 图片打标 → 图片内容审核 → 清理测试对象。
费用：COS 上传/下载 ≈0（极小对象）；CI 打标 1 次 ≈0.0015 元（用户已同意）。

用法（优先 Infisical 注入，屏蔽 .env 旧 TENCENT_SECRET_ID 避免抢占别名优先级）：
  Remove-Item Env:TENCENT_SECRET_ID -ErrorAction SilentlyContinue
  Remove-Item Env:TENCENT_SECRET_KEY -ErrorAction SilentlyContinue
  infisical run --env=dev --silent -- python scripts/smoke_cos.py

输出 JSON：{upload, download, ci_tags, ci_audit, cleaned} 每步 ok/error
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

TEST_KEY = "smoke-test/20260819_cos_smoke.jpg"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    from app.core.config import settings
    from app.services.external import tencent_ci
    from app.services.external.storage import CosStorageBackend

    print(f"cos_bucket={settings.cos_bucket} region={settings.cos_region}", flush=True)
    print(f"tencent key 配置: id={bool(settings.tencent_secret_id)} "
          f"key={bool(settings.tencent_secret_key)}", flush=True)

    # 测试图：优先真实截图（CI 打标需要真实内容），否则仓库演示图/1x1 占位
    shot_dir = Path(os.environ.get("SCREENSHOT_DIR", r"<LOCAL_SCREENSHOTS_DIR>"))
    screenshots = sorted(shot_dir.glob("*.png")) if shot_dir.exists() else []
    candidates = (screenshots[:1] if screenshots else []) + [
        Path(__file__).resolve().parent.parent / "research" / "poc" / "poc03_attribution_demo.jpg",
    ]
    img = next((p for p in candidates if p.exists()), None)
    if img is None or img.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        import base64

        img = Path(__file__).resolve().parent / "smoke_pixel.png"
        img.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        ))
    data = img.read_bytes()
    print(f"测试图: {img.name} ({len(data)} bytes)", flush=True)

    backend = CosStorageBackend()
    report: dict = {}

    # 1. 上传
    try:
        backend.put_object(TEST_KEY, data)
        report["upload"] = {"ok": True}
        print("upload: OK", flush=True)
    except Exception as exc:  # noqa: BLE001
        report["upload"] = {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}
        print(f"upload: FAIL {report['upload']['detail']}", flush=True)
        print(json.dumps(report, ensure_ascii=False))
        return 1

    # 2. 读回校验
    try:
        got = backend.get_object(TEST_KEY)
        report["download"] = {"ok": got == data, "sha256_match": _sha256(got) == _sha256(data)}
        print(f"download: {'OK' if got == data else 'MISMATCH'}", flush=True)
    except Exception as exc:  # noqa: BLE001
        report["download"] = {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}
        print(f"download: FAIL {report['download']['detail']}", flush=True)

    # 3. CI 图片打标（~0.0015 元/次）
    try:
        tags = tencent_ci.image_detect_label(TEST_KEY)
        report["ci_tags"] = {"ok": True, "tags": tags}
        print(f"ci_tags: OK {tags}", flush=True)
    except Exception as exc:  # noqa: BLE001
        report["ci_tags"] = {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}
        print(f"ci_tags: FAIL {report['ci_tags']['detail']}", flush=True)

    # 4. 图片内容审核（S4-03 前置验证）
    try:
        audit = tencent_ci.image_audit(TEST_KEY)
        report["ci_audit"] = {"ok": True, **audit}
        print(f"ci_audit: OK pass={audit['pass']} labels={audit['labels']}", flush=True)
    except Exception as exc:  # noqa: BLE001
        report["ci_audit"] = {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}
        print(f"ci_audit: FAIL {report['ci_audit']['detail']}", flush=True)

    # 5. 清理测试对象
    try:
        backend.delete_object(TEST_KEY)
        report["cleaned"] = {"ok": not backend.object_exists(TEST_KEY)}
        print(f"cleaned: {'OK' if report['cleaned']['ok'] else '仍存在'}", flush=True)
    except Exception as exc:  # noqa: BLE001
        report["cleaned"] = {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}
        print(f"cleaned: FAIL {report['cleaned']['detail']}", flush=True)

    print(json.dumps(report, ensure_ascii=False))
    ok = all(v.get("ok", True) for v in report.values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
