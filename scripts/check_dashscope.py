"""百炼（DashScope）可用性探测（WP-B 条件门）

只读最小调用：qwen-flash 发一次 "ping"，判定 key 是否可用。
欠费/鉴权失败 → status=error，调用方应保持 MOCK_EXTERNAL_AI=true 兜底。

用法：
  infisical run --env=dev -- python scripts/check_dashscope.py
  # 或直接跑（用 backend/.env 的 key）
  python scripts/check_dashscope.py

输出 JSON（stdout）：
  {"status": "ok"|"error"|"skipped", "detail": "<简短原因，不含密钥>"}
退出码：0=ok，1=error，2=skipped
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


def _probe(force: bool = False) -> dict:
    from app.core.config import settings

    if not force and settings.mock_external_ai:
        return {"status": "skipped", "detail": "MOCK_EXTERNAL_AI=true，未走真实调用（--force 可强制探测）"}
    if not settings.dashscope_api_key:
        return {"status": "skipped", "detail": "未配置 DASHSCOPE_API_KEY"}

    try:
        from dashscope import Generation

        resp = Generation.call(
            model="qwen-flash",
            messages=[{"role": "user", "content": "ping"}],
            result_format="message",
            max_tokens=5,
            workspace=settings.dashscope_workspace_id or None,
        )
        if resp.status_code == 200:
            return {"status": "ok", "detail": "qwen-flash 调用成功"}
        # 常见错误码：InvalidApiKey(401)/Arrearage(欠费)/AccessDenied
        return {
            "status": "error",
            "detail": f"HTTP {resp.status_code}: {(getattr(resp, 'message', '') or '')[:120]}",
        }
    except Exception as exc:  # noqa: BLE001 —— 探测脚本需捕获一切
        return {"status": "error", "detail": f"{type(exc).__name__}: {str(exc)[:120]}"}


def main() -> int:
    force = "--force" in sys.argv
    result = _probe(force=force)
    print(json.dumps(result, ensure_ascii=False))
    return {"ok": 0, "error": 1, "skipped": 2}[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
