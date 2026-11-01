"""P2 D-2 补丁预演器 —— 一条命令跑出「现状红 / 补丁后绿」的对照证据。

为什么需要它：`scripts/test_client_utf8_chunk.mjs` 测的是 `client/utils/chat_api.uts` 里
`decodeUtf8` 的**生产原文**。当前生产实现有一个缺陷（整块缓冲区恰好是"未收完的字符"时
会丢 1 字节，并级联毁掉整条回复），但该文件由另一位 owner 负责，本组**不直接改**。
所以这里把拟定的补丁应用到**临时副本**上，再让用例跑那个副本：

    python scripts/preview_utf8_chunk_patch.py

- 生产文件**未被修改**（补丁只写 `%TEMP%/yishu_patch_preview/chat_api.uts`）；
- 退出码 = 用例退出码（0 全绿 / 1 有失败），可直接当门禁用；
- 补丁一旦真的合入生产文件（检测到 `function isTruncatedUtf8(`），本脚本自动改为
  直接测生产文件，不再制造副本 —— 不会变成"永远绿的假证据"。

补丁内容（两处，均为 `chat_api.uts` 的 `decodeUtf8` 邻域）：
1. 新增 `isTruncatedUtf8(bytes)`：按 RFC 3629 判断整块是否「合法但被截断」；
2. `decodeUtf8` 兜底分支：合法截断 ⇒ 返回 `('', 0)`（一个字节都不消费，等下一片）；
   真损坏 ⇒ 保留原行为（丢 1 字节并推进）。
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "client", "utils", "chat_api.uts")
HARNESS = os.path.join(REPO, "scripts", "test_client_utf8_chunk.mjs")

OLD_FALLBACK = (
    '\t// 兜底（正常流不会走到）：连"回退 3 字节"都解不开 ⇒ 流里有**非法字节**（真损坏）。\n'
    "\t// 此时只**丢弃 1 个字节**并保留其余，让同一块里合法的后续内容还能被解出来；\n"
    "\t// 早期版本这里返回 `('', bytes.length)`（整块丢弃）——那会把可读文本一并吃掉，\n"
    '\t// 表现是"偶发少一段回复"，比丢一个坏字节严重得多。\n'
    "\treturn new Utf8Decoded('', bytes.length > 0 ? 1 : 0)\n"
)

NEW_FALLBACK = (
    "\t// 兜底：连「回退 4 字节」都解不开。这里必须**区分两种完全不同的情况**：\n"
    "\t//   (a) 整块就是一个**还没收完的字符**（1~3 字节）—— 如 3 字节汉字被切成 1+2 到达；\n"
    "\t//   (b) 流里真有**非法字节**（真损坏）。\n"
    "\t// 2026-10-30（P2 D-2）：原实现对两者一视同仁地丢 1 字节。对 (a) 来说这是灾难点：\n"
    "\t// pending 从「续字节」开始，之后每一片都解不开、每片再丢 1 字节 ⇒ 不止少一个字，\n"
    "\t// 而是**整条回复全丢**（SSE 帧永远拼不出两个换行，最终只能报「AI 回复未完成」）。\n"
    "\tif (isTruncatedUtf8(bytes)) {\n"
    "\t\treturn new Utf8Decoded('', 0)\n"
    "\t}\n"
    "\treturn new Utf8Decoded('', bytes.length > 0 ? 1 : 0)\n"
)

HELPER = (
    "/**\n"
    " * 整块字节是否「合法但尚未完整」的 UTF-8 前缀（用于把「等下一片」与「真损坏」分开）。\n"
    " *\n"
    " * 按 RFC 3629 逐序列校验：lead byte 决定续字节个数（C2-DF=1 / E0-EF=2 / F0-F4=3），\n"
    " * 且 E0/ED/F0/F4 的**第一个续字节**有额外区间限制（排除 overlong 与 UTF-16 代理区）。\n"
    " * 返回 true 仅当每个已出现的续字节都合法、且最后一个序列在缓冲区末尾**被截断**。\n"
    " */\n"
    "function isTruncatedUtf8(bytes: Uint8Array): boolean {\n"
    "\tlet i = 0\n"
    "\twhile (i < bytes.length) {\n"
    "\t\tconst b = bytes[i]\n"
    "\t\tif (b < 0x80) {\n"
    "\t\t\ti = i + 1\n"
    "\t\t\tcontinue\n"
    "\t\t}\n"
    "\t\tlet need = 0\n"
    "\t\tif (b >= 0xc2 && b <= 0xdf) {\n"
    "\t\t\tneed = 1\n"
    "\t\t} else if (b >= 0xe0 && b <= 0xef) {\n"
    "\t\t\tneed = 2\n"
    "\t\t} else if (b >= 0xf0 && b <= 0xf4) {\n"
    "\t\t\tneed = 3\n"
    "\t\t} else {\n"
    "\t\t\treturn false // 游离续字节 / 0xC0-0xC1 / 0xF5+ ⇒ 真损坏\n"
    "\t\t}\n"
    "\t\tlet got = 0\n"
    "\t\twhile (got < need) {\n"
    "\t\t\tconst idx = i + 1 + got\n"
    "\t\t\tif (idx >= bytes.length) {\n"
    "\t\t\t\treturn true // 末尾还没收完：合法截断，等下一片\n"
    "\t\t\t}\n"
    "\t\t\tconst c = bytes[idx]\n"
    "\t\t\tlet lo = 0x80\n"
    "\t\t\tlet hi = 0xbf\n"
    "\t\t\tif (got == 0) {\n"
    "\t\t\t\tif (b == 0xe0) {\n"
    "\t\t\t\t\tlo = 0xa0\n"
    "\t\t\t\t} else if (b == 0xed) {\n"
    "\t\t\t\t\thi = 0x9f\n"
    "\t\t\t\t} else if (b == 0xf0) {\n"
    "\t\t\t\t\tlo = 0x90\n"
    "\t\t\t\t} else if (b == 0xf4) {\n"
    "\t\t\t\t\thi = 0x8f\n"
    "\t\t\t\t}\n"
    "\t\t\t}\n"
    "\t\t\tif (c < lo || c > hi) {\n"
    "\t\t\t\treturn false // 续字节越界 ⇒ 真损坏，不能等\n"
    "\t\t\t}\n"
    "\t\t\tgot = got + 1\n"
    "\t\t}\n"
    "\t\ti = i + 1 + need\n"
    "\t}\n"
    "\treturn false\n"
    "}\n"
    "\n"
)

ANCHOR_FN_DOC = "/**\n * 字节数组 → UTF-8 文本"


def build_target() -> tuple:
    """返回 (要测的文件路径, 是否预演副本)。锚点缺失/源码异常一律响亮失败。"""
    with open(SRC, encoding="utf-8") as f:
        text = f.read()

    if "function isTruncatedUtf8(" in text:
        # 补丁已在生产文件里：直接测生产文件（本脚本退化为普通回归入口）
        return SRC, False

    if OLD_FALLBACK not in text or ANCHOR_FN_DOC not in text:
        raise SystemExit(
            "[FAIL] 补丁锚点缺失：chat_api.uts 的 decodeUtf8 兜底/文档注释已被改动，"
            "本预演脚本必须同步更新（不允许静默跳过）"
        )

    patched = text.replace(OLD_FALLBACK, NEW_FALLBACK, 1).replace(ANCHOR_FN_DOC, HELPER + ANCHOR_FN_DOC, 1)
    out_dir = os.path.join(tempfile.gettempdir(), "yishu_patch_preview")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "chat_api.uts")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(patched)
    return out, True


def main() -> int:
    target, is_preview = build_target()
    if is_preview:
        print(f"[INFO] 预演：补丁只作用于临时副本 {target}（生产文件未改动）")
    else:
        print(f"[INFO] 生产文件已含 isTruncatedUtf8，直接测 {target}")

    env = dict(os.environ)
    env["YISHU_CHAT_API_PATH"] = target
    proc = subprocess.run(["node", HARNESS], cwd=REPO, env=env, check=False)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
