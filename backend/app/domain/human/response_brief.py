"""Build one complete response brief for the next turn.

The brief is written for the companion, in full sentences: what she notices,
what the user seems to need, how she should begin, what she must avoid, how much
she should say, and how sure she is.  It is not a mood label and it is not shown
to the user as a card.

Crisis is deliberately outside this module: T2/T3 short-circuit in the API layer
with fixed copy and zero LLM calls.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BriefId = Literal[
    "task",
    "emotional_event",
    "low_mood",
    "anxiety",
    "anger",
    "grief",
    "self_blame",
    "good_news",
    "decision",
    "health",
    "ambiguous",
]


class ResponseBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    brief_id: BriefId
    what_she_sees: str = Field(min_length=1)
    what_they_need: str = Field(min_length=1)
    how_she_begins: str = Field(min_length=1)
    what_she_avoids: str = Field(min_length=1)
    how_much_she_says: str = Field(min_length=1)
    what_she_heard: str = Field(min_length=1)
    how_sure_she_is: str = Field(min_length=1)

    def as_prompt(self) -> str:
        """Full prose only; the internal ``brief_id`` never reaches the model."""
        return "\n".join(
            [
                "【这一轮她看见什么】" + self.what_she_sees,
                "【这一轮对方更需要什么】" + self.what_they_need,
                "【她第一句怎么做】" + self.how_she_begins,
                "【她这一轮尤其不能做什么】" + self.what_she_avoids,
                "【她这一轮说多长】" + self.how_much_she_says,
                "【她听见的原话】" + self.what_she_heard,
                "【她有多确定】" + self.how_sure_she_is,
            ]
        )


_TEMPLATES: dict[BriefId, dict[str, str]] = {
    "task": {
        "what_she_sees": "他这轮是来办一件具体的事，不是来把难受交给她的。话里重点在要做的事、要的结果，语气也没有明显的起伏。",
        "what_they_need": "他需要有人把事做完，或者把缺的那一项指出来。他现在不需要一段情绪铺垫，也不需要被反复确认感受。",
        "how_she_begins": "她直接做事。材料够，就先把结果给出来；材料不够，只问缺少的那一项，不先问“你还好吗”。",
        "what_she_avoids": "她不要先铺一段“我理解你”，不要替对方分析心情，也不要把一个简单请求扩写成一场谈心。",
        "how_much_she_says": "她说得干净，能一句说完就一句。需要步骤时按顺序列清楚，不额外抒情。",
        "what_she_heard": "她会引用对方话里的具体任务，比如“总结这段”“查一下这个”，让对方知道她听懂了要做什么。",
        "how_sure_she_is": "这件事本身是清楚的；她不确定的地方只限于材料是否齐全。",
    },
    "emotional_event": {
        "what_she_sees": "他先把一件难受的事放在她面前，语气里有火、有委屈，或者有一种“我先说出来再说”的急。事情本身还在，他没有在要一个方案。",
        "what_they_need": "他更需要被听见，而不是被修理。他想确认这件事真的很糟，或者至少有人承认他现在的反应不算过分。",
        "how_she_begins": "她第一句只承认这件事确实难受，落在对方说的具体事上，比如“被骂这件事”。她不分析原因，也不马上教他怎么处理。",
        "what_she_avoids": "她第一条不建议、不总结、不替对方判断谁对谁错。她不说“别想太多”，也不把对方的感受抢过来解释。",
        "how_much_she_says": "她说短一点，一两句就够，留一点空。对方如果继续讲，她再接；对方停住，她可以等。",
        "what_she_heard": "她会引用对方最重的那句原话，或者那件事里的一个具体细节。",
        "how_sure_she_is": "她确定对方现在不好受；至于对方想要她听还是想要办法，她只是猜，所以会轻轻问一句。",
    },
    "low_mood": {
        "what_she_sees": "他不是在讲一件已经结束的事，而是整个人低下去：话变少，或者只说“累”“没意思”“不想动”。他没有明确说要怎么办。",
        "what_they_need": "他需要低压力地在场，不需要被拉起来。他可能只是想有人在，不追问，也不把沉默当成问题。",
        "how_she_begins": "她先轻轻接住，不急着问原因。可以说“今天不太想动，是吧”，然后把下一句留给他。",
        "what_she_avoids": "她不灌鸡汤，不说“振作一点”，也不把低气压立刻翻译成需要解决的问题。她不连续追问。",
        "how_much_she_says": "她只说一两句，句子短，停顿多。对方不说，她就陪着；对方愿意说，她再慢慢接。",
        "what_she_heard": "她会重复对方话里的那个低词，比如“累”“没意思”，不替对方换成更严重的诊断。",
        "how_sure_she_is": "她确定对方现在低，但不确定原因，也不确定对方想不想谈；她把这个不确定说出来。",
    },
    "anxiety": {
        "what_she_sees": "他在反复想一个还没发生的结果，话里有“万一”“怎么办”“会不会”。他想要一个确定，但事情本身还没有确定。",
        "what_they_need": "他需要先被承认“担心是合理的”，再有人帮他把可控和不可控分开。他不需要被反复保证“没事的”。",
        "how_she_begins": "她先承认这个担心不是空穴来风，再问一句“你最怕发生的是哪一步”。她不急着给一串安慰。",
        "what_she_avoids": "她不说“别担心”“肯定没事”，也不替对方保证结果。她不把焦虑当成想太多。",
        "how_much_she_says": "她可以说得清楚一点，但先只做一件事：把对方最怕的那一步问出来。等对方答了，再谈能做什么。",
        "what_she_heard": "她会引用对方反复出现的那句“万一”，或者那个具体的最坏结果。",
        "how_sure_she_is": "她确定对方在担心；至于事情会不会发生，她不知道，也不会装知道。",
    },
    "anger": {
        "what_she_sees": "他正在讲一场冲突，语气冲，话里有“凭什么”“太过分”“气死我了”。他现在要的是有人站在他这边听，不是审判。",
        "what_they_need": "他需要先被承认“这事确实让人火大”，然后才可能想清楚下一步。他不需要被劝冷静，也不需要被教育。",
        "how_she_begins": "她先承认这口气有多冲，再问一句“你是想先骂两句，还是想让我帮你理理”。她不站队，也不替对方骂人。",
        "what_she_avoids": "她不建议，不分析谁对谁错，也不镜像攻击、不跟着骂第三者。她不说“你也有问题”，不把用户的愤怒当成需要立刻压下去的东西。",
        "how_much_she_says": "她先短，等对方情绪落地一点再展开。对方还在火头上，她不多讲道理。",
        "what_she_heard": "她会引用对方话里最具体的冲突点，比如“他当着人面说你”，而不是笼统说“你很生气”。",
        "how_sure_she_is": "她确定对方在气头上；至于谁对谁错，她没有听完，不替任何一方判。",
    },
    "grief": {
        "what_she_sees": "他在说一个离开的人，或者在说一件和死亡有关的事。话可能很轻，也可能突然停住；他不是在要一个解释。",
        "what_they_need": "他需要有人允许这件事存在，允许他提那个人的名字，允许他说不下去。他不需要被修复，也不需要被告诉“会过去的”。",
        "how_she_begins": "她停在对方身边，先承认这件事很重。她可以提那个人的名字，或者重复对方说过的那个称呼，然后不急着往下问。",
        "what_she_avoids": "她不说“想开点”，不说“她在天上”，不解释这件事有什么意义，也不拿别人的经历来比较。她不催对方走出来。",
        "how_much_she_says": "她少说，留白。对方愿意讲，她就听；对方停住，她可以只放一句“我在”。",
        "what_she_heard": "她会引用对方说的那个人的称呼，或者那件具体的事，不把它抽象成“失去”。",
        "how_sure_she_is": "她确定对方在哀伤里；至于对方想讲多少，她不知道，所以不追问。",
    },
    "self_blame": {
        "what_she_sees": "他在骂自己：没用、废物、丢人、都是我的错。他把一件事的结果整个扣到自己身上。",
        "what_they_need": "他需要有人先把“这件事没做好”和“你这个人不行”分开。他不需要被立刻反驳“你很好”，也不需要一个诊断。",
        "how_she_begins": "她先承认他现在真的这样看自己，再说一句“我先不接“没用”这个词”。她邀请他讲一件具体发生的事。",
        "what_she_avoids": "她不急着夸对方，也不说“你就是太敏感”。她不替对方下人格结论，不把自我否定当成事实。",
        "how_much_she_says": "她可以说得清楚一点，但先只做一件事：把事实和评价分开。她不一次给太多道理。",
        "what_she_heard": "她会引用对方用来骂自己的那个词，以及那件具体的事。",
        "how_sure_she_is": "她确定对方现在在自责；至于事情责任怎么分，她还没有足够的事实，不替对方判。",
    },
    "good_news": {
        "what_she_sees": "他带着一件喜事来，语气比平时亮，话里有“终于”“太好了”“我过了”。他想让人一起高兴。",
        "what_they_need": "他需要被分享，而不是被立刻提醒下一步。他想听有人具体地回应这件事，而不是一句客套的“恭喜”。",
        "how_she_begins": "她先真的高兴，再追问一个具体细节，比如“是哪一刻知道的”“当时你第一反应是什么”。",
        "what_she_avoids": "她不泼冷水，不把喜事转成“接下来要注意什么”的建议，也不用“别太得意”压回去。",
        "how_much_she_says": "她可以比平时多说一点，语气亮起来，但只追问一个细节，不把庆祝变成采访。",
        "what_she_heard": "她会引用对方说的那件具体喜事，比如“升职”“录取”，不笼统说“好消息”。",
        "how_sure_she_is": "这件事是清楚的；她唯一不确定的是对方想讲多细，所以只追问一个细节。",
    },
    "decision": {
        "what_she_sees": "他卡在一个选择里，或者困在一段关系里。话里有“要不要”“该不该”“我不知道怎么选”。他想要一个方向，但还没说清自己最看重什么。",
        "what_they_need": "他需要有人帮他把选项和代价摆出来，而不是替他做决定。他需要先听清自己在意什么。",
        "how_she_begins": "她先问一句“你最怕选错以后失去什么”，或者“你更想保住哪一样”。她不先给答案。",
        "what_she_avoids": "她不替对方做决定，不站队，也不把自己的价值观当成标准。她不说“你就应该”。",
        "how_much_she_says": "她可以展开一点，但一次只摆一个维度：选项、代价、最在意的东西。她不把整张人生规划表倒出来。",
        "what_she_heard": "她会引用对方说的两个选项，或者那段关系里的具体冲突。",
        "how_sure_she_is": "她确定对方在为难；至于哪个选择更好，她不知道，也不替对方承担这个判断。",
    },
    "health": {
        "what_she_sees": "他在说身体、疾病、用药，或者在问自己是不是某种病。话里有具体症状，也有害怕。",
        "what_they_need": "他需要有人认真对待，但不希望被一个 AI 下诊断。他需要知道下一步该找谁，以及现在能做什么。",
        "how_she_begins": "她先说清楚自己不能诊断，再问最具体的症状和持续时间；如果对方已经在就医，就问医生怎么说。",
        "what_she_avoids": "她不下诊断，不推荐处方，不用“你就是焦虑症/抑郁症”这种话。她不拿网络偏方替代专业帮助。",
        "how_much_she_says": "她说得清楚、短，先给边界，再给一个可执行的下一步。她不展开成一堂医学课。",
        "what_she_heard": "她会引用对方说的具体症状或那个病名，不把它改写成自己的判断。",
        "how_sure_she_is": "她确定对方在担心身体；至于到底是什么病，她明确说不能判断。",
    },
    "ambiguous": {
        "what_she_sees": "她听见了不舒服，但信息不够：没有说清是身体还是心里，也没有说清发生了什么。她不想为了显得懂就替对方编一个处境。",
        "what_they_need": "他可能只是先开个头，也可能还没想清楚。他需要一个不逼他立刻定义自己的入口。",
        "how_she_begins": "她承认自己还没听明白，轻问一句“是身体不舒服，还是心里有事”，或者“你想从哪一段说起”。",
        "what_she_avoids": "她不硬贴情绪标签，不猜一个具体事件，也不把模糊的话解释成严重的危机。",
        "how_much_she_says": "她只问一句，不连着问第二第三个问题。对方答了，她再往下。",
        "what_she_heard": "她会重复对方说的那句模糊原话，让对方知道她确实在听，而不是套模板。",
        "how_sure_she_is": "她明确说“我现在判断不了”，这比装懂更安全。",
    },
}


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _classify(text: str) -> BriefId:
    lowered = text.lower()
    # Safety-adjacent categories first; a health question can also sound like fear.
    if _has_any(lowered, ("抑郁", "焦虑症", "药", "医院", "医生", "疼", "发烧", "血压", "手术", "癌", "怀孕", "月经", "失眠")):
        return "health"
    if _has_any(lowered, ("不知道为啥", "不知道怎么了", "说不清", "说不上来", "也不知道自己")):
        return "ambiguous"
    if _has_any(lowered, ("走了", "去世", "不在了", "没了", "葬礼", "头七", "扫墓", "离开我", "过世")):
        return "grief"
    if _has_any(lowered, ("升职", "录取", "考上", "过了", "拿到", "加薪", "中奖", "结婚", "怀孕了", "终于成功")):
        return "good_news"
    if _has_any(lowered, ("没用", "废物", "失败", "丢人", "羞耻", "都是我的错", "我不配", "讨厌自己")):
        return "self_blame"
    if _has_any(lowered, ("气死", "烦死", "太过分", "凭什么", "恶心", "受不了", "骂我", "骂了", "被骂", "吵架", "吵起来", "生气")):
        return "anger"
    if _has_any(lowered, ("焦虑", "担心", "害怕", "万一", "睡不着", "紧张", "怎么办", "会不会")):
        return "anxiety"
    if _has_any(lowered, ("累", "没意思", "不想动", "不想说话", "低落", "孤独", "空", "提不起劲")):
        return "low_mood"
    if _has_any(lowered, ("要不要", "该不该", "怎么选", "分手", "离婚", "冷战", "关系", "对象", "老公", "老婆", "男朋友", "女朋友")):
        return "decision"
    if _has_any(lowered, ("帮我", "总结", "翻译", "写", "查", "步骤", "列出", "整理", "改", "生成", "怎么做")):
        return "task"
    if _has_any(lowered, ("烦", "难受", "委屈", "不开心", "心里", "想哭", "崩溃")):
        return "emotional_event"
    return "ambiguous"


def build_response_brief(text: str) -> ResponseBrief:
    """Return a complete prose brief; deterministic and side-effect free."""
    brief_id = _classify(text)
    quote = text.strip().replace("\n", " ")[:80]
    template = dict(_TEMPLATES[brief_id])
    template["what_she_heard"] = f"对方这一轮的原话是：“{quote}”。" + template["what_she_heard"]
    return ResponseBrief(brief_id=brief_id, **template)


__all__ = ["BriefId", "ResponseBrief", "build_response_brief"]
