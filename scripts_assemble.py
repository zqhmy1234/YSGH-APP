# -*- coding: utf-8 -*-
"""组装器 v2：uvue_gen 生成物 + 现有页面 script → 可编译页面"""
import io, re, os, sys

ROOT = r'D:/GuangH-App/.wt/wrap1-agentA2-ui-restore'
GEN = os.path.join(ROOT, 'uvue_gen')
PAGES = os.path.join(ROOT, 'client', 'pages')

CONFIG = {
    'profile': {
        'gen': 'profile_gen.uvue',
        'target': r'pages/profile/profile.uvue',
        'drop_singles': ['n2_450', 'n2_451', 'n2_528'],
        'drop_blocks': ['n2_504', 'n2_525'],
        'tabbar': 'profile',
        'taps': {
            'n2_467': 'goPortraitManage',
            'n2_475': 'goMessagesCenter',
            'n2_482': 'goSettings',
            'n2_489': 'goPrivacy',
            'n2_496': 'goAbout',
        },
        'extra_functions': '''
	// ardot 直转接线：消息中心入口
	function goMessagesCenter(): void {
		uni.navigateTo({ url: '/pages/messages/messages' })
	}''',
    },
}

def remove_block(tpl, cls):
    # 1) 单行/自闭合元素
    m = re.search(rf'\n\t*<\w+ class="{cls}"[^>]*/>', tpl)
    if m:
        return tpl[:m.start()] + tpl[m.end():]
    # 2) 单行 text：<text class="cls">...</text>
    m = re.search(rf'\n\t*<text class="{cls}"[^>]*>.*?</text>[^\n]*', tpl)
    if m:
        return tpl[:m.start()] + tpl[m.end():]
    # 3) 配平 view 块
    m = re.search(rf'\n(\t*)<view class="{cls}"[^>]*>', tpl)
    if not m:
        print(f'  [warn] {cls} not found')
        return tpl
    start = m.start()
    open_pat = re.compile(r'<view\b')
    close_pat = re.compile(r'</view>')
    rest = tpl[m.end():]
    depth = 1
    idx = 0
    while depth > 0:
        no = open_pat.search(rest, idx)
        nc = close_pat.search(rest, idx)
        if nc is None:
            raise ValueError('unbalanced ' + cls)
        if no and no.start() < nc.start():
            depth += 1
            idx = no.end()
        else:
            depth -= 1
            idx = nc.end()
    return tpl[:start] + tpl[m.end() + idx:]

def add_tap(tpl, cls, fn):
    m = re.search(rf'<view class="{cls}"', tpl)
    if not m:
        print(f'  [warn] tap {cls} not found')
        return tpl
    return tpl[:m.start()] + f'<view class="{cls}" @tap="{fn}"' + tpl[m.end():]

def assemble(key):
    cfg = CONFIG[key]
    gen = io.open(os.path.join(GEN, cfg['gen']), encoding='utf-8').read()
    tm = re.search(r'<template>\n(.*)\n</template>\n\n<style>\n(.*)\n</style>', gen, re.S)
    tpl, style = tm.group(1), tm.group(2)
    for cls in cfg.get('drop_blocks', []):
        tpl = remove_block(tpl, cls)
    for cls in cfg.get('drop_singles', []):
        tpl = remove_block(tpl, cls)
    for cls, fn in cfg['taps'].items():
        tpl = add_tap(tpl, cls, fn)
    tpl = re.sub(r'\n\t</view>\s*$',
                 '\n\t\t<TabBar active="' + cfg["tabbar"] + '" />\n\t</view>', tpl)
    target = os.path.join(ROOT, 'client', cfg['target'])
    src = io.open(target, encoding='utf-8').read()
    sm = re.search(r'(<script setup lang="uts">.*?</script>)', src, re.S)
    script = sm.group(1)
    if 'extra_functions' in cfg:
        marker = cfg['extra_functions'].strip().split('\n')[0].strip()  # 以注释行做幂等标记
        if marker not in script:
            script = script.replace('</script>', cfg['extra_functions'] + '\n</script>')
    out = '<template>\n' + tpl + '\n</template>\n\n' + script + '\n\n<style>\n' + style + '\n</style>\n'
    io.open(target, 'w', encoding='utf-8').write(out)
    print(f'[ok] {target}')

if __name__ == '__main__':
    for k in (sys.argv[1:] or CONFIG.keys()):
        assemble(k)
