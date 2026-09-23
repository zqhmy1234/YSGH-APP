#!/usr/bin/env python3
"""模型接入探针：真实调用一次，验证「模型 ID / base_url / workspace header / 推理开关」四项。

为什么需要常驻（而不是一次性验证）：这四项都是**外部事实**——换 key、换业务空间、换模型、
官方改端点写法之后，只能靠一次真实调用证明配置仍然成立。故固化为可重复执行的运维工具。

成本控制：单轮短提示 + max_tokens=32（约万分之一元级）。
安全：**不打印任何密钥**（api_key 仅以长度与前缀形态出现）。

用法：
  python scripts/probe_model.py
环境来源（**刻意不含 backend/.env**）：
  1) `agent/.env`（本服务的唯一真源）
  2) 进程环境变量（`DASHSCOPE_API_KEY` 等；已设置时优先于文件，`override=False` 保证这点）

  ⚠️ **为什么不回退读 `backend/.env`**（2026-09-23 用户更正后的修正）：
    两者**不是同一个阿里云账号**——Agent 服务必须用 **Token Plan 账号**的 key。
    更危险的是：**用错账号不会报错**（两边都是有效 key，只是账号/额度/业务空间不同），
    探针会"成功"，于是假通过。本脚本因此只认上面两处来源，并**打印实际使用凭证的指纹**
    （长度+前缀+sha256 前 8 位，不可逆、无泄漏），供人工核对账号。
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

env_file = AGENT_ROOT / ".env"
if env_file.exists():
    load_dotenv(env_file, override=False)
    source = "agent/.env"
else:
    source = "进程环境变量（未找到 agent/.env）"
    if not os.getenv("DASHSCOPE_API_KEY"):
        print(
            f"[FAIL] 既无 {env_file}，进程环境变量里也没有 DASHSCOPE_API_KEY。\n"
            "       → 请 `cp agent/.env.example agent/.env` 并填入 **Token Plan 账号**的 key",
            file=sys.stderr,
        )
        raise SystemExit(2)


def fingerprint(value: str) -> str:
    """凭证指纹：长度 + 前缀 + sha256 前 8 位（不可逆，可安全打印）。"""
    return f"len={len(value)} prefix={value[:6]}… sha256[:8]={hashlib.sha256(value.encode()).hexdigest()[:8]}"


from llm_provider import load_llm_settings  # noqa: E402

try:
    settings = load_llm_settings()
except Exception as exc:
    print(f"[FAIL] 配置装载失败：{type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(2) from exc

print(f"[OK] 配置来源：{source}")
print(f"[OK] {settings.describe()}")
print(f"     api_key 指纹：{fingerprint(settings.api_key)}  ← **请核对这是 Token Plan 账号的 key**")
if settings.extra_headers:
    ws = settings.extra_headers.get("X-DashScope-WorkSpace", "")
    print(f"     workspace 指纹：{fingerprint(ws)}（业务空间需与 key 同属 Token Plan 账号）")

from openai import OpenAI  # noqa: E402

client = OpenAI(
    api_key=settings.api_key,
    base_url=settings.base_url,
    default_headers=settings.extra_headers or None,
)
try:
    resp = client.chat.completions.create(
        model=settings.model,
        messages=[{"role": "user", "content": "只回复两个字：收到"}],
        max_tokens=32,
        extra_body={"enable_thinking": settings.enable_thinking},
    )
except Exception as exc:
    print(f"[FAIL] 调用失败：{type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(1) from exc

choice = resp.choices[0]
reasoning = getattr(choice.message, "reasoning_content", None)
print(f"[OK] 调用成功：model={resp.model} finish={choice.finish_reason}")
print(f"     回复={choice.message.content!r}")
print(f"     推理字段：有（{len(reasoning)} 字）" if reasoning else "     推理字段：无")
if resp.usage:
    print(f"     用量：prompt={resp.usage.prompt_tokens} completion={resp.usage.completion_tokens}")
print("\n结论：模型 ID / base_url / workspace header / enable_thinking 四项均实证可用。")
