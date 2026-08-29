"""ai_tagging.py —— BA3 AI 链批：chips 自由标签 + 照片 AI 一句话描述（A1/A7 远期总账）

拍板口径（F1 组）：chips 标签 = AI 动态生成、**不限死枚举**——本服务输出即自由标签
数组（list[str]），与 SetFit 5 类（情绪/灵感/混合/引用/待办，content_class 体系）
互不相关、不作真值。写入列（BA1 已建，勿在此重复建列）：
- contents.tags_json        标签数组（文本/语音类，异步 RQ 任务 tag_content 写）
- contents.ai_description   照片一句话描述（≤40 字，照片管线内联写）

LLM 封装复用（不新造 HTTP 客户端，遵守"不直接 import dashscope"约定）：
- 文本标签：llm_ops.base.chat_text（qwen-flash）——dashscope 统一鉴权
  （_ensure_api_key + workspace 头）+ 统一重试 with_retry(retries=3, backoff=(1,2,4),
  timeout=30)（见 external/retry.py），超时/重试上限由该封装承担，本模块不重复实现。
- 照片描述：external.dashscope.image_caption（Qwen3-VL 图片塔，同一鉴权/重试风格，
  经 _vision 间接调用以便测试 monkeypatch）。

失败语义（不静默原则）：
- LLM 未配置（无 key / MOCK_EXTERNAL_AI=true）/ 调用失败 / 超时 / 输出解析失败 /
  清洗后无有效标签 → 显式抛 AiTaggingError（带 code），**绝不静默返回空数组假装成功**。
- 调用方（pipeline 挂钩 / tag_content 任务）捕获后 log 错误原因；打标是增强项，
  失败不影响主内容 done，tags_json/ai_description 维持 None。
- 开发/测试环境可能没有 Key：函数走「配置缺失」显式路径（LLM_NOT_CONFIGURED），
  不 crash 主流程；worker 启动期已有警告 log（见 workers/worker.py）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.services.llm_ops.base import chat_text, llm_available
from app.services.llm_ops.parsing import extract_json_object

if TYPE_CHECKING:
    from app.db.models import Content

logger = logging.getLogger("yishu.ai_tagging")

# 标签数量与长度约束（拍板：3-6 个、每个 ≤8 字中文）
MAX_TAGS = 6
MAX_TAG_LEN = 8
# 照片描述上限（拍板：一句话 ≤40 字中文）
MAX_DESC_LEN = 40

_TAGS_SYSTEM = (
    "你是记忆整理 App 的内容标签器。根据用户记录的内容，生成 3-6 个用于界面 chips"
    " 展示的中文短标签。\n"
    "规则：\n"
    "1. 标签自由生成，不限于任何固定枚举（主题/情绪/场景/人物/地点等角度皆可）。\n"
    "2. 每个标签不超过 8 个汉字，短语风格（如「产品想法」「妈妈生日」「西湖爬山」）。\n"
    "3. 标签具体、可检索，避免「内容」「记录」这类空泛词。\n"
    "4. 只输出 JSON：{\"tags\": [\"标签1\", \"标签2\", ...]}，不要任何解释。"
)

_DESC_PROMPT = (
    "用一句不超过40字的中文描述这张照片的内容和场景。"
    "直接输出描述本身，不要任何前缀、引号或解释。"
)


class AiTaggingError(RuntimeError):
    """AI 打标/描述失败（不静默：code 供调用方 log 与审计）

    code 取值：
    - LLM_NOT_CONFIGURED  Key 未配置 / mock 模式（配置缺失显式路径）
    - LLM_CALL_FAILED     LLM 调用失败（含超时/重试耗尽）
    - LLM_PARSE_FAILED    输出解析失败 / 清洗后无有效标签
    - EMPTY_TEXT          内容无文本可用
    - IMAGE_NOT_FOUND     照片无可用图片路径
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------- 底层调用（隔离层：测试 monkeypatch 点）
def _chat(system: str, user: str) -> str:
    """qwen-flash 文本对话（转发 base.chat_text，鉴权/重试/超时见 base）"""
    return chat_text(system, user)


def _vision(image_path: str, prompt: str) -> str:
    """Qwen3-VL 图片描述（转发 dashscope.image_caption，同一鉴权/重试风格）"""
    from app.services.external.dashscope import image_caption

    return image_caption(image_path, prompt=prompt)


# --------------------------------------------------------------- 公开服务函数
def generate_tags(content: Content) -> list[str]:
    """Content 行（有转写/正文文本）→ 3-6 个中文短标签（每个 ≤8 字）

    语义：
    - 成功 → 清洗去重后的标签数组（list[str]），保证非空（空结果视为解析失败抛错，
      绝不静默返回空数组假装成功）；条数上限 6，单条超长剔除。
    - LLM 未配置 / 调用失败（含超时、重试 3 次耗尽）/ 输出解析失败 → 抛 AiTaggingError，
      由调用方 log 错误原因（tags_json 维持 None，不阻塞主流程）。
    - 超时与重试上限：由复用的 base.chat_text 内置 with_retry(retries=3, timeout=30) 承担。
    """
    text = (getattr(content, "text", None) or "").strip()
    if not text:
        raise AiTaggingError("EMPTY_TEXT", "内容无文本，无法打标")

    if not llm_available():
        # 配置缺失显式路径：开发/测试环境可能没有 Key（不 crash 主流程，调用方 log）
        raise AiTaggingError("LLM_NOT_CONFIGURED", "百炼未配置（MOCK 或缺 key），AI 打标跳过")

    try:
        raw = _chat(_TAGS_SYSTEM, f"用户记录的内容：\n{text}").strip()
    except Exception as exc:  # noqa: BLE001 —— 统一转为显式错误（含超时/重试耗尽）
        raise AiTaggingError("LLM_CALL_FAILED", f"标签 LLM 调用失败: {exc}") from exc

    tags = _clean_tags(_parse_tags(raw))
    if not tags:
        raise AiTaggingError("LLM_PARSE_FAILED", f"标签输出解析后无有效标签: {raw[:120]}")
    return tags


def generate_photo_description(content: Content, image_path: str | None = None) -> str:
    """照片 Content → 一句 ≤40 字中文描述（Qwen3-VL）

    语义：
    - 成功 → 描述字符串（超长截断到 40 字）。
    - image_path 为空时自行解析：extra.image_path（本地路径）或 cos_key（经存储后端
      下载临时文件）；两者皆无 → 抛 AiTaggingError("IMAGE_NOT_FOUND")。
    - LLM 未配置 / 调用失败 → 抛 AiTaggingError（同 generate_tags 的显式失败语义）。
    - 超时与重试上限：由复用的 dashscope.image_caption 内置 with_retry 承担。
    """
    if not llm_available():
        raise AiTaggingError("LLM_NOT_CONFIGURED", "百炼未配置（MOCK 或缺 key），照片 AI 描述跳过")

    path, tmp_file = _resolve_image(content, image_path)
    try:
        try:
            raw = _vision(str(path), _DESC_PROMPT).strip()
        except Exception as exc:  # noqa: BLE001 —— 统一转为显式错误
            raise AiTaggingError("LLM_CALL_FAILED", f"照片描述 LLM 调用失败: {exc}") from exc
    finally:
        if tmp_file is not None:
            try:
                tmp_file.unlink(missing_ok=True)
            except OSError:
                pass

    if not raw:
        raise AiTaggingError("LLM_PARSE_FAILED", "照片描述输出为空")
    return raw[:MAX_DESC_LEN]


# --------------------------------------------------------------- 内部工具
def _parse_tags(raw: str) -> list[str]:
    """解析 LLM 输出 {"tags": [...]}（容错：围栏剥离 + 花括号切片兜底）"""
    data = extract_json_object(raw)
    hits = data.get("tags") if isinstance(data, dict) else None
    return [h for h in hits if isinstance(h, str)] if isinstance(hits, list) else []


def _clean_tags(hits: list[str]) -> list[str]:
    """标签清洗：剥离空白、去重保序、单条 ≤8 字（超长剔除）、上限 6 个"""
    out: list[str] = []
    for hit in hits:
        tag = str(hit).strip()
        if not tag or tag in out or len(tag) > MAX_TAG_LEN:
            continue
        out.append(tag)
        if len(out) >= MAX_TAGS:
            break
    return out


def _resolve_image(content: Content, image_path: str | None) -> tuple[Path, Path | None]:
    """解析图片本地路径：显式传入 > extra.image_path > cos_key 下载临时文件

    返回 (路径, 需清理的临时文件或 None)；无可用图片 → AiTaggingError。
    """
    if image_path:
        return Path(image_path), None
    extra_path = (getattr(content, "extra", None) or {}).get("image_path")
    if extra_path:
        return Path(extra_path), None
    cos_key = getattr(content, "cos_key", None)
    if cos_key:
        import tempfile

        from app.services.external.storage import get_storage_backend

        data = get_storage_backend().get_object(cos_key)
        tmp = Path(tempfile.gettempdir()) / f"yishu_aidesc_{content.id}.jpg"
        tmp.write_bytes(data)
        return tmp, tmp
    raise AiTaggingError("IMAGE_NOT_FOUND", "照片内容缺少可处理的图片路径")
