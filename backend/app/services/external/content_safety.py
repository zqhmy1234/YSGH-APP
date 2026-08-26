"""用户内容安全审核适配器（B5b #8 · Wave4-L · M3 微信域）

任务卡需求：
- 接口抽象：check_text / check_image —— 统一返回契约 {pass, labels, reason, provider}
- 三实现可插拔：规则（sensitive_words 词库）/ 腾讯 CI（image_audit + moderate 顶替）/
  阿里云（内容安全增强版 Green / 2022-03-02，上架前启用）
- 接入开关：settings.content_safety_provider ∈ {tencent_ci, aliyun, off}（config.py）
  - tencent_ci（当前默认顶替）：文本=规则预检+护栏（moderate）、图片=CI image_audit
  - aliyun（上架前）：文本=规则预检+阿里云 TextModeration、图片=阿里云 ImageBatchModeration
    （⚠️ 需阿里云账号 AccessKey + 开通「内容安全」服务，非百炼 DashScope key；
    缺 key 时抛 RuntimeError = 显式失败，由调用方按 fail-safe 语义降级）
  - off：文本仅本地规则（零外部调用）、图片默认放行——不产生任何审核费用

命中（pass=False）→ 调用方标记 sensitive_status + 不进云端镜像（wechat/service.py 入库点）。

fail-safe 与降级契约（2026-08-26 拍板）：
- 微信收消息链路可靠性优先（M3 门禁：不丢消息 99.9%）——审核服务故障只告警 + 默认放行，
  不因审核不可用而丢弃消息（与 pipeline 对 CI 失败静默降级、tencent_ci._audit_image 一致）。
- 阿里云显式配置但缺 key / 调用失败 → check_* 抛 RuntimeError（配置错误要响，
  否则上架时静默漏审），service.py 捕获后降级放行并告警。

阿里云 HTTP 签名（内容安全增强版 RPC 风格）参考：
- 为通过 HTTP 调用内容检测 API 生成签名：https://help.aliyun.com/zh/document_detail/53415.html
- TextModeration：https://next.api.aliyun.com/document/Green/2022-03-02/TextModeration
- ImageBatchModeration：https://next.api.aliyun.com/document/Green/2022-03-02/ImageBatchModeration
⚠️ 阿里云实现未实网验证（待 key + 开通服务后按真实响应校准 Service 参数与处置映射）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote

logger = logging.getLogger("yishu.content_safety")

# 阿里云内容安全增强版接入点（华北2；其他地域改 ALIYUN_CONTENT_SAFETY_REGION）
ALIYUN_GREEN_ENDPOINT = "https://green-cip.cn-beijing.aliyuncs.com"
ALIYUN_GREEN_SERVICE_VERSION = "2022-03-02"

# 阿里云文本审核服务：content_detection = 内容安全自定义文本检测（通用用户内容）
ALIYUN_TEXT_SERVICE = "content_detection"
# 阿里云图片审核服务：social = 图片社交场景检测（涉政/色情/暴恐/广告等综合）
ALIYUN_IMAGE_SERVICE = "social"


# ---------------------------------------------------------------------------
# 契约：{"pass": bool, "labels": [str], "reason": str, "provider": str}
# ---------------------------------------------------------------------------


def _pass_result(provider: str, reason: str = "") -> dict:
    return {"pass": True, "labels": [], "reason": reason, "provider": provider}


def _block_result(provider: str, labels: list[str], reason: str = "") -> dict:
    return {"pass": False, "labels": labels or [], "reason": reason, "provider": provider}


def _rule_check_text(text: str) -> dict | None:
    """规则层文本预检（零费用、确定性、所有实现共用）：返回拦截结果或 None（通过）"""
    from app.services.external.sensitive_words import check_sensitive

    rule = check_sensitive(text)
    if rule["action"] == "reject":
        return _block_result(
            "rule", list(rule.get("matched") or []), rule.get("reason", "")
        )
    # mask（号码/广告打码）/ pass 均视为不拦截（打码后内容仍入库）
    return None


class ContentSafetyAdapter(ABC):
    """内容安全适配器抽象基类（可插拔：规则 / 腾讯 CI / 阿里云）"""

    name: str = "base"

    @abstractmethod
    def check_text(self, text: str) -> dict:
        """文本审核：命中任一敏感标签 → {"pass": False, labels, ...}"""

    @abstractmethod
    def check_image(self, image_ref: str) -> dict:
        """图片审核：image_ref = 存储 key 或可访问 URL（按实现约定）"""


class OffContentSafety(ContentSafetyAdapter):
    """off：不调外部审核。文本仍走本地规则（免费兜底），图片默认放行。"""

    name = "off"

    def check_text(self, text: str) -> dict:
        blocked = _rule_check_text(text)
        return blocked or _pass_result(self.name, "off 模式仅本地规则")

    def check_image(self, image_ref: str) -> dict:
        return _pass_result(self.name, "off 模式图片默认放行")


class RuleContentSafety(ContentSafetyAdapter):
    """规则实现：文本=开源词库规则（reject 拦截），图片不支持（放行）。"""

    name = "rule"

    def check_text(self, text: str) -> dict:
        blocked = _rule_check_text(text)
        return blocked or _pass_result(self.name)

    def check_image(self, image_ref: str) -> dict:
        return _pass_result(self.name, "规则实现不支持图片审核（默认放行）")


class TencentCiContentSafety(ContentSafetyAdapter):
    """腾讯 CI 实现（当前顶替）：文本=规则+护栏（moderate）、图片=CI image_audit。"""

    name = "tencent_ci"

    def check_text(self, text: str) -> dict:
        from app.services.external import moderate

        guard = moderate(text)
        if not guard["pass"]:
            return _block_result(
                self.name, list(guard.get("matched") or []), guard.get("reason", "")
            )
        return _pass_result(self.name)

    def check_image(self, image_ref: str) -> dict:
        # image_ref = COS key（CI 直接读同桶对象）；CI 不可用 → 默认放行并告警
        # （与 pipeline 对 CI 失败静默降级一致；微信收消息链路不因审核故障丢消息）
        try:
            from app.services.external.tencent_ci import image_audit

            r = image_audit(image_ref)
            if r["pass"]:
                return _pass_result(self.name)
            return _block_result(self.name, list(r.get("labels") or []))
        except Exception as exc:  # noqa: BLE001
            logger.warning("腾讯 CI 图片审核不可用，默认放行 %s: %s", image_ref, exc)
            return _pass_result(self.name, f"tencent_ci 不可用: {exc}")


# ---------------------------------------------------------------------------
# 阿里云内容安全增强版（Green / 2022-03-02）——RPC 风格签名 + HTTP 调用
# ---------------------------------------------------------------------------


def _rfc3986_quote(value: str) -> str:
    """RFC3986 百分号编码：大写十六进制，保留 ~（阿里云签名要求）"""
    return quote(value, safe="~")


def _aliyun_string_to_sign(method: str, params: dict) -> str:
    """规范化查询串（按 key 字典序）→ StringToSign = Method & / & 规范化串"""
    canonical = "&".join(
        f"{_rfc3986_quote(str(k))}={_rfc3986_quote(str(v))}"
        for k, v in sorted(params.items())
    )
    return f"{method.upper()}&{_rfc3986_quote('/')}&{_rfc3986_quote(canonical)}"


def _aliyun_sign(secret: str, string_to_sign: str) -> str:
    """HMAC-SHA1(AccessKeySecret + '&', StringToSign) → Base64"""
    key = (secret + "&").encode("utf-8")
    digest = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def _aliyun_common_params(action: str) -> dict:
    from app.core.config import settings

    if not (settings.aliyun_access_key_id and settings.aliyun_access_key_secret):
        raise RuntimeError(
            "阿里云内容安全未配置：ALIYUN_ACCESS_KEY_ID/ALIYUN_ACCESS_KEY_SECRET"
            "（⚠️ 非百炼 DashScope key，需阿里云账号 AccessKey + 开通「内容安全」服务）"
        )
    return {
        "AccessKeyId": settings.aliyun_access_key_id,
        "Action": action,
        "Format": "JSON",
        "RegionId": settings.aliyun_content_safety_region,
        "ServiceVersion": ALIYUN_GREEN_SERVICE_VERSION,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": str(uuid.uuid4()),
        "SignatureVersion": "1.0",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _aliyun_post(action: str, service: str, service_params: dict) -> dict:
    """发一次内容安全增强版请求：返回完整 JSON 响应（HTTP 失败抛 httpx 异常）"""
    import httpx

    from app.core.config import settings

    params = _aliyun_common_params(action)
    params["Signature"] = _aliyun_sign(
        settings.aliyun_access_key_secret, _aliyun_string_to_sign("POST", params)
    )
    body = {"Service": service, "ServiceParameters": json.dumps(service_params, ensure_ascii=False)}
    resp = httpx.post(
        f"{ALIYUN_GREEN_ENDPOINT}/",
        params=params,
        json=body,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _aliyun_parse_text(data: dict) -> dict:
    """TextModeration 响应：{Code:200, Data:{labels, reason, riskLevel, suggestion, contexts}}

    处置映射（与腾讯 CI HitFlag 对齐）：suggestion=="block" → 拦截；pass/review → 放行（附标签）。
    ⚠️ 未实网验证，待 key 后按真实响应校准（riskLevel high/medium/low 可作补充判定）。
    """
    provider = "aliyun"
    if data.get("Code") not in (200, None):
        raise RuntimeError(f"阿里云文本审核失败: {data.get('Message')}")
    d = data.get("Data") or {}
    labels = [s for s in (str(d.get("labels", "")).split(",")) if s.strip()]
    suggestion = str(d.get("suggestion", "")).lower()
    if suggestion == "block" or (not suggestion and str(d.get("riskLevel", "")).lower() == "high"):
        return _block_result(provider, labels, str(d.get("reason", "")))
    return _pass_result(provider, f"aliyun suggestion={suggestion or 'pass'}")


def _aliyun_parse_image(data: dict) -> dict:
    """ImageBatchModeration 响应：Data.Result[]（每服务一个结果，含 Suggestion/RiskLevel）

    任一服务 suggestion=="block" → 拦截。
    """
    d = data.get("Data") or {}
    if data.get("Code") not in (200, None):
        raise RuntimeError(f"阿里云图片审核失败: {data.get('Message')}")
    labels: list[str] = []
    blocked = False
    for r in d.get("Result") or []:
        sug = str(r.get("Suggestion", "")).lower()
        label = str(r.get("Label", "") or r.get("Labels", ""))
        if sug == "block":
            blocked = True
            if label:
                labels.append(label)
    if blocked:
        return _block_result("aliyun", labels)
    return _pass_result("aliyun")


class AliyunContentSafety(ContentSafetyAdapter):
    """阿里云内容安全实现（上架前启用；当前默认 tencent_ci 顶替）。

    文本：规则预检（免费，先挡确定性命中）→ 阿里云 TextModeration（content_detection）。
    图片：阿里云 ImageBatchModeration（social）——image_ref 需为公网可访问 URL
    （或 OSS 对象参数），COS 私有对象需先经临时 URL / 转存（与腾讯 CI 直读同桶不同）。
    缺 key / 调用失败：抛 RuntimeError（显式失败），由调用方决定降级策略。
    """

    name = "aliyun"

    def check_text(self, text: str) -> dict:
        blocked = _rule_check_text(text)
        if blocked is not None:
            return blocked
        data = _aliyun_post(
            "TextModeration", ALIYUN_TEXT_SERVICE, {"content": text}
        )
        return _aliyun_parse_text(data)

    def check_image(self, image_ref: str) -> dict:
        data = _aliyun_post(
            "ImageBatchModeration",
            ALIYUN_IMAGE_SERVICE,
            {"imageUrl": image_ref},
        )
        return _aliyun_parse_image(data)


# ---------------------------------------------------------------------------
# 注册表与工厂
# ---------------------------------------------------------------------------

_ADAPTERS: dict[str, type[ContentSafetyAdapter]] = {
    "off": OffContentSafety,
    "rule": RuleContentSafety,
    "tencent_ci": TencentCiContentSafety,
    "aliyun": AliyunContentSafety,
}


def get_content_safety(provider: str | None = None) -> ContentSafetyAdapter:
    """按配置 provider 返回内容安全适配器（每次构造轻量实例，支持测试切换）

    provider 缺省读 settings.content_safety_provider（tencent_ci|aliyun|off；
    额外支持 "rule" 纯规则模式供测试/无费用场景）。未知值回退 tencent_ci 并告警。
    """
    from app.core.config import settings

    key = (provider or settings.content_safety_provider or "tencent_ci").lower()
    cls = _ADAPTERS.get(key)
    if cls is None:
        logger.warning("未知 content_safety_provider=%r，回退 tencent_ci", key)
        cls = TencentCiContentSafety
    return cls()


ProviderLiteral = Literal["tencent_ci", "aliyun", "off", "rule"]
