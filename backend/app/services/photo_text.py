"""照片文本的「人话 / 机器产物」拆分（2026-10-26 · 详情页 OCR 糊满正文修复）

## 为什么需要这个模块

照片的 `content.text` 是**两类东西拼在一起的**：

    content.text = f"{caption}\\n{ocr_text}"

- `caption`：Qwen3-VL 一句话描述（「一个人在湖边散步」）——**人话**，用户看得懂；
- `ocr_text`：百度 OCR 的**逐行**识别结果（截图里每个界面词都占一行：
  「此电脑 / 本地磁盘 / (D:) / GuangH-App / deploy / download …」）——**机器产物**。

库内实测一条照片：`text` 858 字符，其中 `ocr_text` 779 字符、`ai_description` 40 字符
⇒ 客户端把 `text` 当正文渲染时，**97% 的正文是截图里的界面文字**。用户看到的
「详情页把照片上的文字全打印出来」就是这个拼接串。

拼在一起对检索是**正确**的（BGE-M3 编码整串，召回不受影响，见 pipeline_photo 注释），
错的是**展示层无法区分**——`ContentOut` 里只有 `text` 一个字段。

## 为什么在服务端拆、而不是客户端自己拆

拼接顺序在 2026-10-25 **翻转过一次**（原 `OCR\\ncaption`，改 `caption\\nOCR`，理由是
OCR 前置会把状态栏噪声顶到最前）。改序**只影响新写入的行**，库里同时躺着两代数据：

- 旧行：`此电脑\\n本地磁盘\\n…\\n一个人在湖边散步`
- 新行：`一个人在湖边散步\\n此电脑\\n本地磁盘\\n…`

客户端若靠「截尾」猜前缀，**旧行会整条露出 OCR**（前缀假设只对新行成立）。
故拆分放在服务端一次做完（两种顺序都认），客户端只按字段渲染。

## 契约口径

`text` 形状**一律不变**（搜索/Qdrant/向量不变、历史行无需回填），只**并列新增**两个字段：
`caption` / `ocr_text`（照片之外的类型两者均为 None）。同 `dimension_evidence` 先例。
"""
from __future__ import annotations


def split_caption_ocr(text: str | None, ocr: str | None) -> tuple[str | None, str | None]:
    """把照片 `text`（caption + OCR 拼接串）拆回 (caption, ocr_text)。

    参数
    ----
    text : `contents.text` 原文（照片 = caption 与 OCR 的拼接串；可 None）
    ocr : `contents.extra["ocr_text"]` 原文（无 OCR 时为 None）

    返回
    ----
    (caption, ocr_text)：
      · 无 OCR（或 ocr 为空串）→ `(text, None)` —— 照片只有 caption，原样即人话；
      · 有 OCR → caption 为去掉 OCR 那一段后的剩余文本（全空则 None），ocr_text 原样返回。

    边界
    ----
    · `text == ocr`（caption 生成失败、text 只有 OCR）→ `(None, ocr)`：正文应为空，
      由客户端走「无正文」形态，**不要**把 OCR 顶上来当正文（这就是要修的病）；
    · 拼接串损坏（OCR 既不在头也不在尾，如被截断）→ 整串当 caption 返回。
      宁可多显示一段，也不吞掉用户可能真写了的内容；
    · 只做顺序判定与剥离，不改写 OCR 内部换行（换行是 OCR 的原始形态，展示层自行归并）。
    """
    if not ocr:
        return text, None
    if not text:
        return None, ocr
    if text == ocr:
        return None, ocr
    # 2026-10-25 之后的写法：`caption\nOCR`
    tail = "\n" + ocr
    if text.endswith(tail):
        head = text[: -len(tail)].strip()
        return (head or None), ocr
    # 2026-10-25 之前的写法：`OCR\ncaption`
    head_prefix = ocr + "\n"
    if text.startswith(head_prefix):
        rest = text[len(head_prefix) :].strip()
        return (rest or None), ocr
    # 拼接串异常（截断/手工改过）：不做假设，整串当人话
    return text, ocr
