"""组装器 v2：uvue_gen 生成物 + 现有页面 script → 可编译页面"""
import os
import re
import sys

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

DROP_NAMES = ['状态栏时间', '电池', 'Home指示条', '玻璃TabBar', '中央＋按钮']

def remove_by_name(tpl):
    # 单行：<!-- 名字 --> 结尾的自闭合/文本行
    for nm in DROP_NAMES:
        pat = re.compile(r'\n\t*<(?:image|text|view) class="[^"]+"[^>]*/>\s*<!--[^>]*' + nm + '[^>]*-->')
        while True:
            m = pat.search(tpl)
            if not m:
                break
            tpl = tpl[:m.start()] + tpl[m.end():]
    # 配平块：玻璃TabBar 等 view 块
    for nm in DROP_NAMES:
        pat = re.compile(r'\n(\t*)<view class="[^"]+"> <!--[^>]*' + nm + '[^>]*-->')
        while True:
            m = pat.search(tpl)
            if not m:
                break
            start = m.start()
            rest = tpl[m.end():]
            depth, idx = 1, 0
            op = re.compile(r'<view\b'); cl = re.compile(r'</view>')
            while depth > 0:
                no = op.search(rest, idx); nc = cl.search(rest, idx)
                if nc is None: raise ValueError('unbalanced ' + nm)
                if no and no.start() < nc.start():
                    depth += 1
                    idx = no.end()
                else:
                    depth -= 1
                    idx = nc.end()
            tpl = tpl[:start] + tpl[m.end() + idx:]
    return tpl

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

def add_tap_by_name(tpl, name, fn):
    # 匹配 <view class="..."> <!-- 名字 -->（名字为注释主体）
    pat = re.compile(r'<view class="(n[0-9_]+)"( @tap="[^"]*")?(> <!-- ' + re.escape(name) + ' -->)')
    m = pat.search(tpl)
    if m:
        return tpl[:m.start()] + f'<view class="{m.group(1)}" @tap="{fn}"{m.group(3)}' + tpl[m.end():]
    # image 自闭合形式：<!-- icon: 名字 -->
    pat2 = re.compile(r'<image class="(n[0-9_]+)" (src="[^"]*")( @tap="[^"]*")? /> <!-- icon: ' + re.escape(name) + ' -->')
    m2 = pat2.search(tpl)
    if m2:
        return tpl[:m2.start()] + f'<image class="{m2.group(1)}" {m2.group(2)} @tap="{fn}" /> <!-- icon: {name} -->' + tpl[m2.end():]
    print(f'  [warn] name-tap {name} not found')
    return tpl

def add_tap(tpl, cls, fn):
    m = re.search(rf'<view class="{cls}"', tpl)
    if not m:
        print(f'  [warn] tap {cls} not found')
        return tpl
    return tpl[:m.start()] + f'<view class="{cls}" @tap="{fn}"' + tpl[m.end():]

def assemble(key):
    cfg = CONFIG[key]
    if cfg.get('skip'):
        print(f'[skip] {key}'); return
    gen = open(os.path.join(GEN, cfg['gen']), encoding='utf-8').read()
    tm = re.search(r'<template>\n(.*)\n</template>\n\n<style>\n(.*)\n</style>', gen, re.S)
    tpl, style = tm.group(1), tm.group(2)
    tpl = remove_by_name(tpl)
    for cls in cfg.get('drop_blocks', []):
        tpl = remove_block(tpl, cls)
    for cls in cfg.get('drop_singles', []):
        tpl = remove_block(tpl, cls)
    for cls, fn in cfg.get('taps', {}).items():
        tpl = add_tap(tpl, cls, fn)
    for name, fn in cfg.get('name_taps', {}).items():
        tpl = add_tap_by_name(tpl, name, fn)
    if cfg.get('tabbar'):
        tpl = re.sub(r'\n\t</view>\s*$',
                     '\n\t\t<TabBar active="' + cfg["tabbar"] + '" />\n\t</view>', tpl)
    target = os.path.join(ROOT, 'client', cfg['target'])
    src = open(target, encoding='utf-8').read()
    sm = re.search(r'(<script setup lang="uts">.*?</script>)', src, re.S)
    script = sm.group(1)
    if 'extra_functions' in cfg:
        marker = cfg['extra_functions'].strip().split('\n')[0].strip()  # 以注释行做幂等标记
        if marker not in script:
            script = script.replace('</script>', cfg['extra_functions'] + '\n</script>')
    out = '<template>\n' + tpl + '\n</template>\n\n' + script + '\n\n<style>\n' + style + '\n</style>\n'
    open(target, 'w', encoding='utf-8').write(out)
    print(f'[ok] {target}')


CONFIG.update({
    'ai': {
        'gen': 'ai_gen.uvue', 'target': r'pages\\detail\\detail.uvue',  # AI 对话页暂未建独立页面，复用 detail 占位？否——跳过组装
        'skip': True,
    },
    'search': {
        'gen': 'search_gen.uvue', 'target': r'pages/search/search.uvue',
        'tabbar': 'search',
        'name_taps': {
            'chip全部': 'setFilterAll', 'chip文字': 'setFilterText',
            'chip语音': 'setFilterVoice', 'chip照片': 'setFilterPhoto',
        },
        'extra_functions': """
	// ardot 直转接线：筛选 chips
	function setFilterAll(): void { setFilter('all') }
	function setFilterText(): void { setFilter('text') }
	function setFilterVoice(): void { setFilter('voice') }
	function setFilterPhoto(): void { setFilter('photo') }""",
    },
    'messages': {
        'gen': 'messages_gen.uvue', 'target': r'pages/messages/messages.uvue',
        'tabbar': None,
        'name_taps': {
            '返回': 'goBack',
        },
    },
    'settings': {
        'gen': 'settings_gen.uvue', 'target': r'pages/settings/settings.uvue',
        'tabbar': None,
        'name_taps': {
            '返回': 'goBack',
            '行-账号与安全': 'goAccount',
            '行-通知偏好': 'toggleNotify',
            '行-深色模式': 'cycleTheme',
            '行-清理缓存': 'clearCache',
            '开关': 'toggleNotify',
        },
    },
    'interview': {
        'gen': 'interview_gen.uvue', 'target': r'pages/interview/interview.uvue',
        'tabbar': None,
        'name_taps': {
            '麦克风钮': 'onMicTap',
        },
    },
    'detail': {
        'gen': 'detail_gen.uvue', 'target': r'pages/detail/detail.uvue',
        'tabbar': None,
        'name_taps': {
            '返回钮': 'goBack',
            '收藏钮': 'toggleFav',
            '更多钮': 'onMore',
            '操作-问AI': 'askAI',
        },
    },
})

if __name__ == '__main__':
    for k in (sys.argv[1:] or CONFIG.keys()):
        assemble(k)
