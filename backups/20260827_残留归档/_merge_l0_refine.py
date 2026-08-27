#!/usr/bin/env python3
"""合并 L0 9 个细化片段到主文件。按 id 对齐，替换 values / values_detail / value_template，
并把 refine_note 追加进 note。备份原文件。"""
import glob
import json
import os
import shutil

MASTER = r"D:\GuangH-App\docs\画像维度枚举集_l0.json"
BAK = r"D:\GuangH-App\docs\画像维度枚举集_l0.bak.json"
FRAG_DIR = r"D:\GuangH-App\docs"

REPLACE_FIELDS = ["values", "values_detail", "value_template"]

def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def main():
    master = load(MASTER)
    shutil.copyfile(MASTER, BAK)
    dims = {d["id"]: d for d in master["dimensions"]}
    n_found = 0
    n_changed = 0
    frags = sorted(glob.glob(os.path.join(FRAG_DIR, "_l0_refine_*.json")))
    print(f"发现片段 {len(frags)} 个: {[os.path.basename(f) for f in frags]}")
    for fp in frags:
        frag = load(fp)
        for fd in frag.get("dimensions", []):
            did = fd["id"]
            if did not in dims:
                print(f"  [WARN] 片段 {os.path.basename(fp)} 含未知维度 {did}，跳过")
                continue
            n_found += 1
            md = dims[did]
            changed = False
            for fld in REPLACE_FIELDS:
                if fld in fd and fd[fld] not in (None, [], ""):
                    md[fld] = fd[fld]
                    changed = True
            rn = fd.get("refine_note")
            if rn:
                old_note = md.get("note", "")
                md["note"] = (old_note + f" 【细化v2】{rn}").strip()
                changed = True
            if changed:
                n_changed += 1
            # 强制保 open_enum
            md["open_enum"] = True
    # 写回
    with open(MASTER, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False, indent=2)
    # 校验
    total = len(master["dimensions"])
    print(f"\n合并完成: 匹配维度 {n_found}/{total}, 实际修改 {n_changed}")
    print(f"备份: {BAK}")
    # 校验报告
    print("\n=== 校验 & 各维度值数 ===")
    missing = []
    for d in master["dimensions"]:
        v = d.get("values")
        if not isinstance(v, list) or len(v) == 0:
            missing.append(d["id"])
        if d.get("open_enum") is not True:
            missing.append(d["id"] + "(open_enum)")
        print(f"  {d['id']:<22} {len(v) if isinstance(v,list) else '??':>3} 值   two_level={d.get('two_level')}")
    print(f"\n总计 {total} 维; 异常: {missing if missing else '无'}")
    assert total == 51, f"维度数应为51，实际{total}"  # noqa: S101
    assert not missing, f"存在异常维度: {missing}"  # noqa: S101
    print("✅ 校验通过（51 维齐全、values 非空、open_enum=true）")

if __name__ == "__main__":
    main()
