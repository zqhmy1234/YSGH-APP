"""
设计数据提取管线 v4（正源版）：
使用 etree 直接遍历的解析器（已跑通并生成 design_gap_audit 的那套），
代替 svgelements 版 parse_svg_v2/v3（会崩 'Circle' object has no attribute 'r' 致 elements=0）。
复用 .cowork-temp/svg_parse.py 的 parse_svg / cluster_text_runs。
输出到 design_data_v4/。
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '.cowork-temp'))

from svg_parse import parse_svg, cluster_text_runs  # noqa: E402, I001

DESIGN_DIR = r'C:\Users\ghf\Downloads\忆述光华 · W1 时间轴页方向小样'
OUTPUT_DIR = os.path.join(_HERE, 'design_data_v4')

CORE_PAGES = [
    ('定稿 · 精修高保真.svg', '01_时间轴主页'),
    ('搜索页.svg', '02_搜索页'),
    ('消息中心.svg', '03_消息中心'),
    ('我的页.svg', '04_我的页'),
    ('记录面板.svg', '05_记录面板'),
    ('冷启动访谈.svg', '06_冷启动访谈'),
    ('设置页.svg', '07_设置页'),
    ('记忆详情页.svg', '08_记忆详情页'),
]

AUX_FILES = [
    ('TabBar胶囊.svg', 'TabBar胶囊'),
    ('中央＋按钮.svg', '中央＋按钮'),
    ('补充态 · 空状态.svg', '补充态_空状态'),
]


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    summary = {}
    print('=== 正源解析 → design_data_v4 ===')
    for filename, name in CORE_PAGES + AUX_FILES:
        src = os.path.join(DESIGN_DIR, filename)
        if not os.path.exists(src):
            print('  ⚠️ 缺失: ' + filename)
            continue
        out = parse_svg(src)
        out['text_runs'] = cluster_text_runs(out)
        out['page_name'] = name
        # 仅保留干净字段
        out.pop('_raw_images', None)
        out.pop('_patterns', None)
        dst = os.path.join(OUTPUT_DIR, name + '.json')
        with open(dst, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        q = {
            'rects': len(out['rects']),
            'circles': len(out['circles']),
            'paths': len(out['paths']),
            'images': len(out['images']),
            'text_runs': len(out['text_runs']),
            'gradients': len(out['gradients']),
            'gradient_has_dir': sum(1 for g in out['gradients'] if not g.get('radial') and g.get('x1') and g.get('x2')),
            'filters': len(out['filters']),
            'filter_has_shadow': sum(1 for f in out['filters'] if 'shadow' in f),
        }
        summary[name] = q
        status = '✅' if (q['rects'] + q['circles'] + q['paths']) > 0 else '❌'
        print('  ' + status + ' ' + name + ': rects=' + str(q['rects']) + ' circles=' + str(q['circles'])
              + ' paths=' + str(q['paths']) + ' texts=' + str(q['text_runs'])
              + ' gradients=' + str(q['gradients']) + ' (dir=' + str(q['gradient_has_dir']) + ')'
              + ' filters=' + str(q['filters']) + ' (shadow=' + str(q['filter_has_shadow']) + ')')
    with open(os.path.join(OUTPUT_DIR, '_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    bad = [k for k, v in summary.items() if (v['rects'] + v['circles'] + v['paths']) == 0]
    print('=== 完成 === 共 ' + str(len(summary)) + ' 份；零几何文件数 = ' + str(len(bad)) + ' ' + str(bad))


if __name__ == '__main__':
    main()
