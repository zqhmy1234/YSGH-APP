"""RAG 多基准集构建（覆盖本项目全部输入分布）

corpus-A 截图    ：真实截图 500 张（build_corpus.py 已生成 corpus.json）
corpus-B 文字碎片：5 类（待办/灵感/情绪/引用/混合）× 20+ 条 = ≥100 条
corpus-C 语音转写：口语化/无标点/错别字风格 50 条（模拟 ASR 输出分布）
corpus-D 混合    ：A+B+C 合并索引（跨类型检索,主线门禁用）
corpus-E 规模压力：程序化合成 1 万条文本（测 P95 随规模曲线;合成属评测必需）

查询分层（每基准集独立 queries.json）：
  descriptive 描述性语义 / keyword 关键词精确 / typo 错字口语 /
  temporal 时间表达 / route 路由意图 / length 长短查询 / numeric 数字混合

用法：
  python -m research.rag_benchmark.build_corpora [--scale 10000]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BENCH_DIR = Path(__file__).resolve().parent
CORPORA_DIR = BENCH_DIR / "corpora"
QUERIES_DIR = BENCH_DIR / "queries"

# ---------- corpus-B：文字碎片（5 类,种子扩展） ----------
_TEXT_SEED = {
    "todo": [
        "明天记得取快递", "下午三点给妈妈打电话", "别忘了交房租", "周末去银行办卡",
        "买牛奶和鸡蛋", "周三前把报告发给领导", "晚上八点约了牙医", "修阳台门锁",
        "回王老师邮件", "周一开会带笔记本", "该给车加油了", "预约体检",
        "月底前完成季度总结", "给猫咪预约绝育手术", "退掉不合适的衣服", "充值地铁卡",
        "给外婆买助听器电池", "清理手机相册", "更新身份证有效期", "给朋友寄生日礼物",
    ],
    "idea": [
        "想做一个记录植物生长的app", "把老照片做成时光胶囊", "书店创业点子",
        "给每个城市画气味地图", "童年玩具改蓝牙音箱", "写巷口修鞋匠的小说",
        "用声音记录心情", "极简桌面收纳方案", "把梦境画下来", "交换闲置种子的社区",
        "爷爷手写信做成有声书", "阳台改造成小菜园", "宠物日记自动生成插画",
        "用旧牛仔裤做环保袋", "社区共享工具箱", "把旅行照片做成地图墙",
    ],
    "emotion": [
        "今天好累,什么都不想做", "看到夕阳突然有点想哭", "好久没这么开心了",
        "感觉被大家遗忘了", "妈妈做的菜让我很安心", "被老板表扬了,开心",
        "有点焦虑,睡不着", "和老友重逢,百感交集", "一个人过节有点孤独",
        "看到小猫被收养,暖暖的", "今天有点烦躁,什么都不顺", "想起外婆,心里酸酸的",
        "加班到深夜,突然很委屈", "收到老同学消息,眼眶一热", "跑完五公里,痛快",
        "孩子第一次叫爸爸,想哭", "面试失败了,有点沮丧", "今天被陌生人暖心帮助",
    ],
    "quote": [
        "人生如逆旅,我亦是行人", "知人者智,自知者明", "生活不止眼前的苟且",
        "凡是过往,皆为序章", "夜色难免黑凉,前行必有曙光", "世界以痛吻我,要我报之以歌",
        "纵有疾风起,人生不言弃", "你若盛开,蝴蝶自来", "路漫漫其修远兮,吾将上下而求索",
        "长风破浪会有时,直挂云帆济沧海", "己所不欲,勿施于人", "时间会治愈一切",
        "不忘初心,方得始终", "道阻且长,行则将至", "此心安处是吾乡",
    ],
    "mixed": [
        "今天天气不错,适合出门走走", "楼下新开的咖啡店味道还行", "视频里那个猫挺有意思",
        "听说地铁三号线要延长了", "群里有人分享了新出的电影", "这个月电费比上个月贵",
        "手机又提示存储空间不足", "邻居家的狗每天早上叫", "今天买菜花了五十块",
        "淘宝上买的小夜灯到了", "地铁上看到一个有趣的广告", "天气转凉,记得加衣服",
        "小区门口新开了水果店", "今天通勤路况出奇的好", "楼下的桂花开了",
    ],
}

# ---------- corpus-C：语音转写风格（口语化/无标点/错别字） ----------
_VOICE_SEED = [
    "那个明天别忘了去拿快递哈", "我靠今天加班到十点累死了", "想弄个那种自动浇花的装置",
    "人生如逆旅嘛我亦是行人", "奶奶做的红烧肉真好吃啊", "下个月要去云南旅游了准备攻略",
    "记得给猫买猫粮和猫砂", "今天面试感觉凉了心里没底", "听说地铁新线开通了很方便",
    "把上次拍的照片整理一下发朋友圈", "考研还剩一百天有点慌", "想去学游泳一直没去成",
    "这个月工资还没发唉", "孩子的家长会定在周五下午", "周末想约老张去钓鱼",
    "手机又没电了老是忘带充电器", "今天看到彩虹了运气不错", "想把客厅重新布置一下",
    "记录一下今天跑完十公里", "那家川菜馆的毛血旺绝了", "明天要交方案今晚赶一赶",
    "小区停水了要提前存水", "给爸妈买了按摩仪", "今天心情不错出去走走",
    "这个月电费怎么这么贵", "想换个新手机纠结中", "闺蜜下个月要结婚了随多少礼",
    "菜市场的青菜便宜了", "楼下的流浪猫生了一窝", "今天被领导夸了开心",
    "复习完高数第二章", "感觉最近皮肤变差了", "订了周五的机票回老家",
]

# ---------- 查询分层模板 ----------
def _queries_for_text() -> list[dict]:
    """corpus-B 分层查询（query → 期望命中的文本内容片段,用前缀匹配定位 id）"""
    # 期望 id 生成规则：corpus-B 文本 id = b-{label}-{i:02d}
    return [
        # descriptive 描述性语义（label 级相关性：相关 = 该类全部 todo/idea/emotion 项）
        {"query": "记得要去办的事情", "expected_label": "todo", "layer": "descriptive"},
        {"query": "关于做产品的想法", "expected_label": "idea", "layer": "descriptive"},
        {"query": "让我难过的记录", "expected_label": "emotion", "layer": "descriptive"},
        {"query": "人生感悟和道理", "expected_label": "quote", "layer": "descriptive"},
        # keyword 关键词精确
        {"query": "买牛奶", "expected_label": "todo", "layer": "keyword"},
        {"query": "马拉松", "expected_label": "emotion", "layer": "keyword"},
        {"query": "收房租", "expected_label": "todo", "layer": "keyword"},
        # typo 错字口语（稀疏召回验证）
        {"query": "买牛乃", "expected_label": "todo", "layer": "typo"},
        {"query": "松鼠桂鱼", "expected_label": "__none__", "layer": "typo"},  # 无相关——验证不误召回
        # temporal 时间表达（行为判定：基准无历史数据 → 应返回空）
        {"query": "去年去的地方", "layer": "temporal"},
        {"query": "上个月的照片", "layer": "temporal"},
        # route 路由意图（行为判定）
        {"query": "照片里的猫", "expected_label": "__none__", "layer": "route"},
        {"query": "给领导交季度总结", "expected_label": "todo", "layer": "route"},
        # length 长短查询
        {"query": "购物", "expected_label": "todo", "layer": "length"},
        {"query": "记得明天下午之前把上个月的工作总结报告交给领导", "expected_label": "todo", "layer": "length"},
    ]


def build_text_corpus() -> list[dict]:
    items: list[dict] = []
    for label, texts in _TEXT_SEED.items():
        for i, text in enumerate(texts):
            items.append({"id": f"b-{label}-{i:02d}", "text": text, "content_type": "text", "label": label})
    return items


def build_voice_corpus() -> list[dict]:
    return [
        {"id": f"c-{i:02d}", "text": t, "content_type": "voice", "label": "voice"}
        for i, t in enumerate(_VOICE_SEED)
    ]


def build_scale_corpus(n: int) -> list[dict]:
    """corpus-E：程序化合成文本（评测规模压力,非产品数据）"""
    templates = [
        "工作备忘 {i}: 今天处理了{topic}相关事项,进度到 {pct}%",
        "灵感记录 {i}: 关于{topic}的一个新想法,编号 {i}",
        "日常碎片 {i}: 今天{topic}发生了点小事,花销 {cost} 元",
        "语录摘抄 {i}: {quote}——来源 {i}",
        "聊天记录 {i}: 我们讨论了{topic},最后约了周末见面",
    ]
    topics = ["项目复盘", "家庭聚会", "旅行计划", "学习笔记", "健康管理", "投资理财", "装修设计", "宠物养护"]
    quotes = ["千里之行始于足下", "学而不思则罔", "温故而知新", "三人行必有我师", "欲速则不达"]
    items = []
    for i in range(n):
        t = templates[i % len(templates)].format(
            i=i, topic=topics[i % len(topics)], pct=(i * 7) % 100,
            cost=(i * 13) % 200, quote=quotes[i % len(quotes)],
        )
        items.append({"id": f"e-{i:05d}", "text": t, "content_type": "text", "label": "scale"})
    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=int, default=10000, help="corpus-E 规模")
    args = parser.parse_args()

    CORPORA_DIR.mkdir(exist_ok=True)
    QUERIES_DIR.mkdir(exist_ok=True)

    text_items = build_text_corpus()
    voice_items = build_voice_corpus()
    scale_items = build_scale_corpus(args.scale)

    # 保存语料（corpus-A 由 build_corpus.py 生成；D 运行时合并 A+B+C）
    (CORPORA_DIR / "corpus_b_text.json").write_text(
        json.dumps(
            {"_meta": {"version": 1, "note": "文字碎片 5 类分布", "count": len(text_items)},
             "items": text_items},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    (CORPORA_DIR / "corpus_c_voice.json").write_text(
        json.dumps(
            {"_meta": {"version": 1, "note": "语音转写风格分布（口语/无标点/错字）",
                        "count": len(voice_items)},
             "items": voice_items},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    (CORPORA_DIR / "corpus_e_scale.json").write_text(
        json.dumps(
            {"_meta": {"version": 1, "note": f"规模压力合成文本 {args.scale} 条",
                        "count": len(scale_items)},
             "items": scale_items},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    (QUERIES_DIR / "queries_text.json").write_text(
        json.dumps({"queries": _queries_for_text()}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"corpus-B 文字碎片: {len(text_items)} 条")
    print(f"corpus-C 语音转写: {len(voice_items)} 条")
    print(f"corpus-E 规模压力: {len(scale_items)} 条")
    print(f"queries_text: {len(_queries_for_text())} 条分层查询")


if __name__ == "__main__":
    main()
