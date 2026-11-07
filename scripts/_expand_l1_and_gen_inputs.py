"""展开 L1 两个超细容器(84 facet) + 为全部维度生成每维输入文件"""
import json
import os
import shutil

L1 = "docs/画像维度枚举集_l1_骨架.json"
BAK = "docs/画像维度枚举集_l1_骨架.json.bak"
IN_DIR = "docs/_l1_in"
REF_DIR = "docs/_l1_refine"

# ---- 确切 84 facet 中文名（来自 E:/.../matraix_schema/dimensions.json）----
BIG5_ZH = {
 "big5_imagination":"想象力","big5_artistic_interest":"艺术兴趣","big5_emotionality":"情绪感受性",
 "big5_adventurousness":"冒险倾向","big5_intellect":"智识兴趣","big5_liberalism":"观念开放性",
 "big5_self_efficacy":"自我效能","big5_orderliness":"条理性","big5_dutifulness":"尽责守信",
 "big5_achievement_striving":"成就驱动","big5_self_discipline":"自律","big5_cautiousness":"谨慎",
 "big5_friendliness":"亲和友善","big5_gregariousness":"合群性","big5_assertiveness":"主见主张",
 "big5_activity_level":"活跃程度","big5_excitement_seeking":"刺激寻求","big5_cheerfulness":"开朗乐天",
 "big5_trust":"信任他人","big5_morality":"道德感","big5_altruism":"利他心","big5_cooperation":"配合协作",
 "big5_modesty":"谦逊","big5_sympathy":"同情心","big5_anxiety":"焦虑倾向","big5_anger":"易怒倾向",
 "big5_depression":"消沉倾向","big5_self_consciousness":"羞怯敏感","big5_immoderation":"节制力(反向)",
 "big5_vulnerability":"承压脆弱",
 "bfi2_domain_extraversion":"外向性(域)","bfi2_domain_agreeableness":"宜人性(域)",
 "bfi2_domain_conscientiousness":"尽责性(域)","bfi2_domain_negative_emotionality":"负向情绪性(域)",
 "bfi2_domain_open_mindedness":"开放思维(域)",
 "bfi2_facet_sociability":"社交亲和","bfi2_facet_assertiveness":"果敢表达","bfi2_facet_energy_level":"精力充沛",
 "bfi2_facet_compassion":"共情关怀","bfi2_facet_respectfulness":"尊重礼让","bfi2_facet_trust":"信任(二阶)",
 "bfi2_facet_organization":"条理组织","bfi2_facet_productiveness":"高效产出","bfi2_facet_responsibility":"责任担当",
 "bfi2_facet_anxiety":"焦虑(二阶)","bfi2_facet_depression":"消沉(二阶)","bfi2_facet_emotional_volatility":"情绪易变",
 "bfi2_facet_intellectual_curiosity":"智识好奇","bfi2_facet_aesthetic_sensitivity":"审美敏感",
 "bfi2_facet_creative_imagination":"创造想象",
}
CHAR_ZH = {
 "domain_characteristics":"性格总览","dominant_trait":"主导特质","trait_curiosity":"好奇心","trait_creativity":"创造力",
 "trait_love_of_learning":"好学钻研","trait_open_mindedness":"开明包容","trait_perspective":"通透洞察",
 "trait_bravery":"勇敢","trait_perseverance":"坚毅","trait_honesty":"诚实正直","trait_zest":"热忱投入",
 "trait_capacity_for_love":"爱与被爱","trait_kindness":"善良温厚","trait_social_intelligence":"人情练达",
 "trait_teamwork":"团队协作","trait_fairness":"公道公平","trait_leadership":"领导力","trait_forgiveness":"宽容大度",
 "trait_humility":"谦卑","trait_prudence":"审慎克制","trait_self_regulation":"自我调节","trait_appreciation_of_beauty":"审美欣赏",
 "trait_gratitude":"感恩","trait_hope_optimism":"乐观希望","trait_playfulness":"玩心童趣","trait_spirituality":"精神信仰",
 "trait_ambition":"雄心抱负","trait_empathy":"共情力","trait_resilience":"韧性抗挫","trait_discipline":"纪律约束",
 "trait_generosity":"慷慨大方","trait_loyalty":"忠诚可靠","trait_competitiveness":"好胜心","trait_adaptability":"适应变通",
}

LEVEL5 = ["很高","高","平均","低","很低"]

def build_superfine():
    out = []
    for mid, zh in BIG5_ZH.items():
        out.append({"id":mid,"label":zh,"category":"超细性格",
            "values":list(LEVEL5),"value_template":"level5","source":"M","matraix_id":mid,
            "confidence_threshold":0.8,
            "note":"方案D超细；Big Five 5档枚举+三层披露+证据锚点；行为锚点见 values_detail",
            "open_enum":True})
    for mid, zh in CHAR_ZH.items():
        out.append({"id":mid,"label":zh,"category":"超细性格",
            "values":list(LEVEL5),"value_template":"level5","source":"M","matraix_id":mid,
            "confidence_threshold":0.8,
            "note":"方案D超细；Character 5档枚举+三层披露+证据锚点；行为锚点见 values_detail",
            "open_enum":True})
    return out

# ---- 加载 L1 ----
if not os.path.exists(BAK):
    shutil.copy(L1, BAK)
d = json.load(open(L1, encoding="utf-8"))
dims = d["dimensions"]
vt = d.get("value_templates", {})

# 展开：用 84 个替换 big5_group / character_group
supers = build_superfine()
new_dims = []
replaced = 0
for x in dims:
    if x["id"] in ("big5_group","character_group"):
        new_dims.extend(supers if replaced == 0 else [])  # 第一个容器位置放全部 84
        replaced += 1
        if replaced == 2:
            # 第二个容器位置不再放（84 已在第一次放入）
            pass
    else:
        new_dims.append(x)
# 若只有一个容器或顺序问题，确保 84 恰好插入一次：
# 上面的逻辑：第一个容器处插入 84，第二个容器跳过 → 共 111-2+84=193
d["dimensions"] = new_dims
json.dump(d, open(L1,"w",encoding="utf-8"), ensure_ascii=False, indent=2)
print("展开后总维数:", len(d["dimensions"]), " (应为193)  替换容器数:", replaced)

# ---- 生成每维输入文件 ----
os.makedirs(IN_DIR, exist_ok=True)
os.makedirs(REF_DIR, exist_ok=True)
def resolve_template(name):
    return vt.get(name) if name else None
def refine_mode(x):
    # 凡有 value_template：values 是档位锚点（程度/等级/频率/是否），保留档位、补中国语境行为锚点
    if x.get("value_template"):
        return "template_level"
    if x.get("structure"):
        return "non_enum_structure"
    if x.get("values"):
        return "explicit_values"
    return "non_enum_structure"
count = 0
for x in d["dimensions"]:
    mode = refine_mode(x)
    rec = {
        "id": x["id"], "label": x.get("label"), "category": x.get("category"),
        "current_values": x.get("values"), "value_template": x.get("value_template"),
        "template_values": resolve_template(x.get("value_template")),
        "structure": x.get("structure"), "source": x.get("source"),
        "matraix_id": x.get("matraix_id"), "confidence_threshold": x.get("confidence_threshold"),
        "note": x.get("note"), "open_enum": x.get("open_enum"),
        "refine_mode": mode,
    }
    json.dump(rec, open(f"{IN_DIR}/{x['id']}.json","w",encoding="utf-8"), ensure_ascii=False, indent=2)
    count += 1
print("输入文件数:", count)
