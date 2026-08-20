"""敏感词规则引擎（B5b 护栏规则层升级 · 2026-08-20）

设计（用户拍板：两档处置 + 开源词表 + 号码全拦）：
- 词表类（涉政/色情/涉枪爆/广告）→ 整条拦截（action=reject）
- 号码类（身份证/手机/银行卡，正则）→ 打码保存（action=mask，保留回忆主体）
- 归一化：去空格/全半角/大小写 → 防简单变体绕过
- 词表：Apache-2.0 开源词库（backend/data/sensitive/，含 LICENSE），按类加载
- 无词表文件时回退内置最小词表（不阻断系统启动）

返回：{"pass": bool, "action": "reject"|"mask"|"pass", "reason": str,
       "masked_text": str|None, "matched": [词], "categories": [类]}
"""
from __future__ import annotations

import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("yishu.sensitive")

# 数据目录：backend/data/sensitive（sensitive_words.py 位于 backend/app/services/external/）
_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "sensitive"

# 号码正则（中文环境 \b 失效——中文与数字间无 ASCII 边界，用前后向断言）
_RE_ID_CARD = re.compile(r"(?<![\d])[1-9]\d{16}[\dXx](?![\d])")   # 18 位身份证
_RE_PHONE = re.compile(r"(?<![\d])1[3-9]\d{9}(?![\d])")           # 11 位手机号
_RE_BANK_CARD = re.compile(r"(?<![\d])\d{16,19}(?![\d])")         # 银行卡

# 内置兜底词表（词表文件缺失时用；真实词库加载后仍补充这些高频词）
_FALLBACK_WORDS = {
    "政治类": ["习近平", "法轮功", "六四", "天安门事件"],
    "色情类": ["裸聊", "招嫖", "儿童色情", "约炮", "援交", "卖淫"],
    "涉枪涉爆": ["枪支", "炸弹", "炸药", "枪支弹药", "土制炸弹", "爆炸物"],
    "广告类": ["代开发票", "刷单", "博彩", "赌博"],
}

# 分类文件 → 类别名
_CATEGORY_FILES = {
    "政治类.txt": "政治类",
    "色情类.txt": "色情类",
    "涉枪涉爆违法信息关键词.txt": "涉枪涉爆",
    "广告.txt": "广告类",
    "网址.txt": "违规网址",
}

# 词表类 → 处置（两档：reject 整条拦截；mask 打码保留）
_CATEGORY_ACTION = {
    "政治类": "reject",
    "色情类": "reject",
    "涉枪涉爆": "reject",
    "广告类": "mask",   # 广告词打码即可（如"代开发票"替换）
    "违规网址": "mask",  # 违规域名打码（保留文本主体；用户 2026-08-20 拍板先接入）
}

# 网址黑名单：1.5 万域名，禁止子串匹配（太慢）→ 提取域名后集合 O(1) 查询
_RE_URL_DOMAIN = re.compile(
    r"(?i)(?:https?://|www\.)?([a-z0-9][a-z0-9-]*\.(?:com|cn|net|org|cc|top|xyz|vip|club|site|online|wang|work|info|biz|me|tv|io|app|dev|pro)(?:\.[a-z]{2})?)"
)


def _normalize(text: str) -> str:
    """归一化：全角→半角、去空白、小写（防简单变体绕过）"""
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"[\s\u3000]", "", t)
    return t.lower()


def _load_word_file(path: Path) -> set[str]:
    """加载逗号/换行分隔的词表文件（去空、去尾逗号）"""
    words: set[str] = set()
    raw = path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        tokens = [t.strip() for t in line.split(",") if len(t.strip()) >= 2]
        words.update(tokens)
    return words


@lru_cache(maxsize=1)
def _load_words() -> dict[str, set[str]]:
    """加载全部词表文件（缓存；文件缺失回退内置）"""
    result: dict[str, set[str]] = {}
    for fname, cat in _CATEGORY_FILES.items():
        p = _DATA_DIR / fname
        if p.exists():
            result[cat] = _load_word_file(p)
        else:
            result[cat] = set()
        # 合并内置补充词（开源词库缺的高频词，2026-08-20 实测）
        result[cat] |= set(_FALLBACK_WORDS.get(cat, []))
    return result


def _load_url_domains() -> set[str] | None:
    """网址黑名单域名集合（独立加载；文件缺失返回 None = 不启用网址检测）"""
    p = _DATA_DIR / "网址.txt"
    if not p.exists():
        return None
    return _load_word_file(p)


def _mask_number(text: str, pattern: re.Pattern, keep_head: int, keep_tail: int) -> tuple[str, int]:
    """号码打码：保留头尾，中间 *（如 138****5678）；返回 (新文本, 打码数量)"""
    def _rep(m: re.Match) -> str:
        v = m.group(0)
        if len(v) <= keep_head + keep_tail:
            return v
        return v[:keep_head] + "*" * (len(v) - keep_head - keep_tail) + v[-keep_tail:]

    new_text, n = pattern.subn(_rep, text)
    return new_text, n


def _check_urls(text: str) -> tuple[str, list[str]]:
    """提取文本中的域名 → 查黑名单集合 → 命中域名打码

    返回 (打码后文本, 命中域名列表)。黑名单未配置返回原文本。
    """
    domains = _load_url_domains()
    if not domains:
        return text, []
    hits: list[str] = []
    masked = text

    def _rep(m: re.Match) -> str:
        domain = m.group(1).lower()
        if domain in domains:
            hits.append(domain)
            return m.group(0).replace(domain, "*" * len(domain))
        return m.group(0)

    masked = _RE_URL_DOMAIN.sub(_rep, masked)
    return masked, hits


def _mask_normalized_in_original(text: str, normalized: str, words: list[str]) -> tuple[str, list[str]]:
    """归一化命中的词 → 映射回原文打码（审查修复 P1-13）

    原实现：matched 来自 normalized，却用原文 replace —— 全角/空格变体（如
    "代 开发 票"）normalized 命中但原文 replace 落空，广告词未被实际打码。
    方案：逐字符建立 normalized→原文 的映射（原文中空白/全角字符在归一化后
    被折叠，映射时对齐到对应的原文字符），命中词按 normalized 区间回填
    原文对应区间的所有字符为 *。
    """
    # 建立 normalized 每个字符 → 原文索引 的映射
    norm_to_orig: list[int] = []
    i = 0
    for ch in normalized:
        # 从当前原文位置起，找到第一个归一化后非空且与 ch 匹配的字符
        while i < len(text):
            nc = _normalize(text[i])
            if nc and nc == ch:
                break
            i += 1
        if i < len(text):
            norm_to_orig.append(i)
            i += 1
        else:
            norm_to_orig.append(len(text) - 1)

    masked = list(text)
    actually_masked: list[str] = []
    for w in words:
        start = 0
        while True:
            idx = normalized.find(w, start)
            if idx < 0:
                break
            orig_start = norm_to_orig[idx] if idx < len(norm_to_orig) else idx
            orig_end = (
                norm_to_orig[idx + len(w) - 1] + 1
                if idx + len(w) - 1 < len(norm_to_orig)
                else orig_start + len(w)
            )
            for k in range(orig_start, min(orig_end, len(masked))):
                masked[k] = "*"
            actually_masked.append(w)
            start = idx + len(w)
    return "".join(masked), actually_masked


def check_sensitive(text: str) -> dict:
    """敏感检测主入口 → {"pass", "action", "reason", "masked_text", "matched", "categories"}"""
    if not text or not text.strip():
        return {"pass": True, "action": "pass", "reason": "", "masked_text": text,
                "matched": [], "categories": []}

    normalized = _normalize(text)
    words = _load_words()

    # 0. 网址黑名单检测（独立路径：提取域名 → 集合 O(1)；1.5 万条不做子串匹配）
    #    审查 CRITICAL 修复：不再提前 return——与词表检测同时执行，reject 恒优先于 mask，
    #    防止"敏感词+黑名单网址"同现时 reject 语义被 URL 分支旁路。
    url_masked, url_hits = _check_urls(text)

    # 1. 词表类检测
    matched: list[str] = []
    categories: list[str] = []
    for cat, wset in words.items():
        for w in wset:
            if w and w in normalized:
                matched.append(w)
                categories.append(cat)
                break  # 每类记一个即可
    if matched:
        # 按最严重类别处置（政治/色情/涉枪爆 = reject；广告 = mask）
        actions = {_CATEGORY_ACTION[c] for c in categories}
        if "reject" in actions:
            if url_hits:
                categories = categories + ["违规网址"]
            return {"pass": False, "action": "reject",
                    "reason": f"命中敏感词: {matched[0]}",
                    "masked_text": None, "matched": matched, "categories": categories}
        # 仅广告类 → 打码（整条保留，词替换；审查 P1-13：在原文上映射打码，
        # 修复全角/空格变体命中但原文 replace 落空的问题）
        masked, _ = _mask_normalized_in_original(text, normalized, matched)
        if url_hits:
            masked = url_masked
            categories = categories + ["违规网址"]
        return {"pass": True, "action": "mask",
                "reason": f"广告词已打码: {matched[0]}",
                "masked_text": masked, "matched": matched, "categories": categories}

    # 2. 网址黑名单（无词表命中时单独处置：打码保留文本主体）
    if url_hits:
        return {"pass": True, "action": "mask",
                "reason": f"{len(url_hits)} 个违规网址已打码",
                "masked_text": url_masked, "matched": url_hits, "categories": ["违规网址"]}

    # 2. 号码类检测（打码保留，身份证/手机/银行卡）
    masked = text
    n = 0
    masked, n1 = _mask_number(masked, _RE_ID_CARD, keep_head=3, keep_tail=4)
    n += n1
    masked, n2 = _mask_number(masked, _RE_PHONE, keep_head=3, keep_tail=4)
    n += n2
    masked, n3 = _mask_number(masked, _RE_BANK_CARD, keep_head=4, keep_tail=4)
    n += n3
    if n:
        return {"pass": True, "action": "mask",
                "reason": f"{n} 处号码已打码",
                "masked_text": masked, "matched": [], "categories": ["隐私号码"]}

    return {"pass": True, "action": "pass", "reason": "", "masked_text": text,
            "matched": [], "categories": []}
