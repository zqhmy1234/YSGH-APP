#!/usr/bin/env python3
"""合并 193 个 L1 细化片段到主文件 docs/画像维度枚举集_l1_骨架.json。
规则：
  - template_level   : 保持 values 不变（模板五档/频率等），注入 values_detail（每档中国语境行为锚点）
  - non_enum_structure: 保持 structure 与 values，注入 values_detail（中国语境值池）
  - explicit_values  : 用 fragment.values 覆盖（中国语境升级后的显式类别）
并把 fragment.refine_note 追加进 dim.note；标记 refined=true / refined_at。
"""
import json
import os
from datetime import date

MAIN = "docs/画像维度枚举集_l1_骨架.json"
REFINE_DIR = "docs/_l1_refine"
OUT = MAIN  # 原地合并（已提前备份 .merge_bak）

def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def mode_of(dim):
    if dim.get("value_template"):
        return "template_level"
    if dim.get("structure"):
        return "non_enum_structure"
    if dim.get("values"):
        return "explicit_values"
    return "non_enum_structure"

def main():
    main_doc = load(MAIN)
    dims = main_doc["dimensions"]
    ids = [d["id"] for d in dims]
    assert len(ids) == len(set(ids)), "主文件存在重复 id!"  # noqa: S101
    stats = {"explicit": 0, "template": 0, "nonenum": 0, "missing_frag": 0, "no_detail": 0}
    today = date.today().isoformat()  # noqa: DTZ011

    for dim in dims:
        did = dim["id"]
        frag_path = os.path.join(REFINE_DIR, f"{did}.json")
        if not os.path.exists(frag_path):
            stats["missing_frag"] += 1
            print(f"  [WARN] 缺失片段: {did}")
            continue
        frag = load(frag_path)
        mode = mode_of(dim)
        # 交叉校验片段 refine_mode
        fm = frag.get("refine_mode")
        if fm and fm != mode:
            print(f"  [NOTE] {did} 主文件mode={mode} 片段mode={fm} → 以主文件为准")

        if mode == "explicit_values":
            new_vals = frag.get("values")
            if isinstance(new_vals, list) and new_vals:
                dim["values"] = new_vals
                stats["explicit"] += 1
            else:
                print(f"  [WARN] {did} explicit 但片段 values 无效，保留原值")
            detail = frag.get("values_detail")
            if detail:
                dim["values_detail"] = detail  # 显式值也保留中国语境示例锚点
                stats["explicit_detail"] = stats.get("explicit_detail", 0) + 1
        elif mode == "template_level":
            detail = frag.get("values_detail")
            if detail:
                dim["values_detail"] = detail
                stats["template"] += 1
            else:
                stats["no_detail"] += 1
        else:  # non_enum_structure
            detail = frag.get("values_detail")
            if detail:
                dim["values_detail"] = detail
                stats["nonenum"] += 1
            else:
                stats["no_detail"] += 1

        # 标记 + 追加 refine_note
        dim["refined"] = True
        dim["refined_at"] = today
        rn = frag.get("refine_note")
        if rn:
            sep = "；" if dim.get("note") else ""
            dim["note"] = (dim.get("note") or "") + sep + "【细化】" + rn

    # 写回
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(main_doc, f, ensure_ascii=False, indent=2)

    # 校验
    doc2 = load(OUT)
    d2 = doc2["dimensions"]
    assert len(d2) == 193, f"合并后维度数={len(d2)} 非193!"  # noqa: S101
    assert len({x['id'] for x in d2}) == 193, "存在重复 id!"  # noqa: S101
    bad_open = [x['id'] for x in d2 if x.get('open_enum') is not True]
    assert not bad_open, f"open_enum 非 true: {bad_open}"  # noqa: S101
    with_detail = sum(1 for x in d2 if x.get('values_detail'))
    print("==== 合并完成 ====")
    print(f"总维度: {len(d2)}")
    print(f"explicit_values 覆盖 values: {stats['explicit']}")
    print(f"template_level 注入锚点:     {stats['template']}")
    print(f"non_enum_structure 注入池:   {stats['nonenum']}")
    print(f"缺失片段: {stats['missing_frag']}  无 values_detail: {stats['no_detail']}")
    print(f"带 values_detail 的维度: {with_detail}")
    print(f"open_enum 全部 true: {not bad_open}")
    print(f"JSON 合法 ✔  输出: {OUT}")

if __name__ == "__main__":
    main()
