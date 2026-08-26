"""查询侧 NER 实体抽取（B2-2 · 2026-08-19）

设计：查询"苏州"/"小张" → place/tag 过滤条件（RET-007 时间+地点约束）。
策略：规则词表起步（免费、确定性、零延迟——P95<3s 门禁下不增 LLM 往返），
      LLM 兜底预留（enable_llm=True 时 qwen-flash 抽取，失败自动回落规则结果）。

地点词表：中国省级行政区 + 主要城市（保守词表：只认行政区划与知名地标，
          避免"公园/学校"这类普通名词被误当 place 过滤导致空结果）。
人物：小X / X先生·女士·老师·同学 / 亲属称谓（爸爸·妈妈·爷爷…）。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("yishu.ner")

# 省级行政区（含简称形态：河北/河北省/河北人 → 归一为省名）
_PROVINCES = [
    "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
    "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
]

# 主要城市（省会 + 计划单列 + 知名地级市；匹配优先级高于省份）
_CITIES = [
    "石家庄", "太原", "呼和浩特", "沈阳", "大连", "长春", "哈尔滨", "南京", "苏州",
    "无锡", "常州", "南通", "杭州", "宁波", "温州", "嘉兴", "绍兴", "金华", "合肥",
    "福州", "厦门", "泉州", "南昌", "济南", "青岛", "烟台", "潍坊", "郑州", "武汉",
    "长沙", "广州", "深圳", "珠海", "佛山", "东莞", "中山", "惠州", "南宁", "桂林",
    "海口", "三亚", "成都", "重庆", "贵阳", "昆明", "拉萨", "西安", "兰州", "西宁",
    "银川", "乌鲁木齐", "台北", "高雄", "台中", "台南", "香港", "澳门",
]

# 知名地标/景区（与城市同权，匹配即 place）
_LANDMARKS = [
    "外滩", "故宫", "西湖", "长城", "兵马俑", "张家界", "九寨沟", "丽江", "大理",
    "黄山", "泰山", "华山", "峨眉山", "鼓浪屿", "三亚湾", "亚龙湾", "迪士尼",
    "环球影城", "颐和园", "天安门", "东方明珠", "黄浦江", "拙政园", "寒山寺",
]

# 人物模式：小X / X先生·女士·老师·同学 / 亲属称谓
_RE_PERSON = re.compile(
    r"(小[\u4e00-\u9fa5]|[\u4e00-\u9fa5]{1,2}(?:先生|女士|老师|同学|同事|朋友|阿姨|叔叔|爷爷|奶奶|外公|外婆|哥哥|姐姐|弟弟|妹妹|爸爸|妈妈|儿子|女儿))"
)

_LLM_EXTRACT_SYSTEM = (
    "你是记忆检索的实体抽取器。从查询中抽取地点（place）和人物（person），"
    "输出 JSON：{\"place\": \"\" 或地名, \"person\": \"\" 或人名}。"
    "只输出 JSON，不要解释。没有就留空字符串。"
)


def _extract_place_rules(query: str) -> str | None:
    """规则地点抽取：最长优先（城市>省份>地标），避免"江苏"吞"苏州市"短匹配"""
    # 前置剥离常见方位动词（去/在/到…），避免被后缀正则吞进匹配（"去景德镇"）
    q = re.sub(r"^(?:去了|去过|去|在|到|从|回|来|上|前往|去往|于|位于)", "", query)
    for cand in sorted(_CITIES + _LANDMARKS + _PROVINCES, key=len, reverse=True):
        if cand in q:
            return cand
    # 后缀形态：XX省 / XX市 / XX区 / XX县（未入表的地名也能捕获）
    m = re.search(r"([\u4e00-\u9fa5]{2,6}(?:省|自治区|特别行政区))", q)
    if m:
        return m.group(1)
    m = re.search(r"([\u4e00-\u9fa5]{2,6}(?:市|地区|州|盟|镇))", q)
    if m:
        return m.group(1)
    return None


def _extract_person_rules(query: str) -> str | None:
    m = _RE_PERSON.search(query)
    return m.group(1) if m else None


def extract_entities(query: str, enable_llm: bool = False) -> dict:
    """查询 → {"place": str|None, "person": str|None}

    enable_llm=True 且规则未命中时尝试 qwen-flash 兜底（真实模式）；
    任何异常回落规则结果（NER 是增强项，不阻断检索主链路）。
    """
    place = _extract_place_rules(query)
    person = _extract_person_rules(query)

    if enable_llm and not (place or person):
        try:
            llm = _extract_llm(query)
            place = place or llm.get("place") or None
            person = person or llm.get("person") or None
        except Exception as exc:  # noqa: BLE001 —— NER 失败不影响检索
            logger.debug("LLM NER 失败，回落规则: %s", exc)

    return {"place": place, "person": person}


def _extract_llm(query: str) -> dict:
    """qwen-flash 兜底抽取（真实模式；失败抛异常由调用方处理）

    S1-H1/L7 收口：统一走 llm_ops.base.chat_text（此前直接 import dashscope._chat_text，
    绕过统一入口的日志/降级策略）+ llm_ops.parsing.extract_json_object（此前内联
    find("{")/rfind("}") 切片解析、无围栏剥离）。
    """
    from app.services.llm_ops.base import chat_text
    from app.services.llm_ops.parsing import extract_json_object

    answer = chat_text(_LLM_EXTRACT_SYSTEM, query).strip()
    data = extract_json_object(answer)
    return {k: (v or "").strip() for k, v in data.items() if isinstance(v, str)}
