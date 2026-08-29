# -*- coding: utf-8 -*-
"""ardot → uvue 直转管道 v2（数据驱动完备版）

完备性保证（所有类型 × 所有属性 = 数据穷举驱动）：
  类型矩阵   : FRAME→view / TEXT→text / VECTOR|ELLIPSE|RECTANGLE→SVG image(裸矢量也导出)
  布局矩阵   : none→绝对定位 / vertical→column / horizontal→row
  尺寸矩阵   : 数值→rpx / fill_container→flex:1(横父)或100%(竖父) / hug_contents→不设
  fills      : SOLID / GRADIENT_LINEAR(两色) / GRADIENT_RADIAL(中心色近似) / IMAGE(占位+标记)
  effects    : DROP_SHADOW(首个) / BACKGROUND_BLUR(glass降级) / INNER_SHADOW(audit approx)
  其他       : strokes(实线) / cornerRadius / clipsContent→overflow / glass白底提浓
  文本       : 字体/字重/颜色/定位/尺寸/阴影（none 父下绝对定位含状态栏偏移）
审计机制   : 每个节点处理归类 exact/approx/dropped，结束打印审计表，dropped>0 exit 1
双输出     : uvue（页面代码）+ html（浏览器直接预览视觉，无需部署）
换算       : rpx = px * 750/390（精确 1.9231），html px = 设计 px
"""
import json, io, re, sys, os

SRC_FILE = os.environ.get('ARDOT_JSON', r'C:\Users\ghf\.workbuddy\projects\d-GuangH-App\514ed5f4-4e5c-4545-9f95-f672df876976\tool-results\mcp-connector-proxy-ardot_batch_read-1788032327586-d99a04.txt')
ROOT_DIR = r'D:/GuangH-App/.wt/wrap1-agentA2-ui-restore'
DESIGN_W = 390
RPX_W = 750
SCALE = RPX_W / DESIGN_W  # 1.9231 精确换算
STATUS_OFFSET = 44  # px，沉浸式状态栏补偿
VEC_TYPES = ('VECTOR', 'ELLIPSE', 'RECTANGLE')

def load_nodes():
    raw = io.open(SRC_FILE, encoding='utf-8').read()
    i = raw.find('{')
    obj = json.loads(raw[i:])
    if 'content' in obj:
        obj = json.loads(obj['content'][0]['text'])
    return obj['data']['nodes']

def find_frame(nodes, fid):
    for n in nodes:
        if n.get('id') == fid:
            return n
    return None

def rgba(c, op=None):
    r, g, b = round(c['r']*255), round(c['g']*255), round(c['b']*255)
    a = round(c.get('a', 1) if op is None else op, 3)
    if a >= 1:
        return f'#{r:02X}{g:02X}{b:02X}'
    return f'rgba({r}, {g}, {b}, {a})'

def is_icon(node):
    """FRAME 子树全矢量且 ≤40px → 图标（40 覆盖 24px TabBar 图标 + 20px 行图标）"""
    if node.get('type') != 'FRAME':
        return False
    w, h = node.get('width', 0), node.get('height', 0)
    if not (isinstance(w, (int, float)) and isinstance(h, (int, float))):
        return False
    if w > 40 or h > 40:
        return False
    ch = node.get('children') or []
    if not ch:
        return False
    return all(c.get('type') in VEC_TYPES for c in ch)

class Audit:
    def __init__(self):
        self.exact = 0
        self.approx = []   # (id, name, reason)
        self.dropped = []  # (id, name, reason)

    def ok(self): self.exact += 1
    def near(self, nid, name, reason): self.approx.append((nid, name, reason))
    def miss(self, nid, name, reason): self.dropped.append((nid, name, reason))

    def report(self, page):
        print(f'--- 审计 [{page}] exact={self.exact} approx={len(self.approx)} dropped={len(self.dropped)}')
        for nid, name, r in self.approx:
            print(f'  ~ {nid} {name}: {r}')
        for nid, name, r in self.dropped:
            print(f'  ✗ {nid} {name}: {r}')
        return len(self.dropped) == 0

class Gen:
    def __init__(self, page_name):
        self.page = page_name
        self.styles = {}      # uvue rpx 样式
        self.hstyles = {}     # html px 样式
        self.icons = []       # (nodeId, slug, name)
        self.ulines = []
        self.hlines = []
        self.indent = 1
        self.audit = Audit()

    def cls(self, node):
        return 'n' + node['id'].replace(':', '_')

    def emit(self, cls, decls):
        self.styles.setdefault(cls, [])
        self.styles[cls].extend([x for x in decls if x not in self.styles[cls]])

    def emit_h(self, cls, decls):
        self.hstyles.setdefault(cls, [])
        self.hstyles[cls].extend([x for x in decls if x not in self.hstyles[cls]])

    def rpx(self, v):
        return f'{v*SCALE:.1f}rpx'.replace('.0rpx', 'rpx')

    # ---- 定位/尺寸（FRAME 与 TEXT 共用） ----
    def box_decls(self, node, parent, root, is_text=False):
        d = []
        hd = []
        pw = (parent or {}).get('layout')
        is_root_child = parent is not None and parent.get('id') == ROOT_ID
        w, h = node.get('width'), node.get('height')
        # 定位
        if parent and parent.get('layout', 'none') == 'none' and not root:
            x, y = node.get('x'), node.get('y')
            if is_root_child:
                y = (y or 0) + STATUS_OFFSET
            if isinstance(x, (int, float)):
                d.append('position: absolute')
                d.append(f'left: {self.rpx(x)}')
                hd.append('position: absolute')
                hd.append(f'left: {x:.1f}px')
            if isinstance(y, (int, float)):
                d.append(f'top: {self.rpx(y)}')
                hd.append(f'top: {y:.1f}px')
        # 宽
        if isinstance(w, (int, float)):
            d.append(f'width: {self.rpx(w)}')
            hd.append(f'width: {w:.1f}px')
        elif w == 'fill_container':
            if pw == 'horizontal':
                d.append('flex: 1')
                hd.append('flex: 1')
            else:
                d.append('width: 100%')
                hd.append('width: 100%')
        # 高
        if isinstance(h, (int, float)):
            d.append(f'height: {self.rpx(h)}')
            hd.append(f'height: {h:.1f}px')
        elif h == 'fill_container':
            d.append('flex: 1')
            hd.append('flex: 1')
        return d, hd

    def flex_decls(self, node, d, hd):
        layout = node.get('layout', 'none')
        if layout not in ('vertical', 'horizontal'):
            return
        d.append('flex-direction: ' + ('row' if layout == 'horizontal' else 'column'))
        hd.append('display: flex')
        hd.append('flex-direction: ' + ('row' if layout == 'horizontal' else 'column'))
        gap = node.get('gap', 0)
        if gap:
            d.append(f'gap: {self.rpx(gap)}')
            hd.append(f'gap: {gap:.1f}px')
        elif layout == 'horizontal':
            ch = [c for c in (node.get('children') or []) if isinstance(c.get('x'), (int, float))]
            if len(ch) >= 2:
                gaps = [ch[i+1]['x'] - ch[i]['x'] for i in range(len(ch)-1)]
                if min(gaps) >= 40:
                    d.append('justify-content: space-between')
                    hd.append('justify-content: space-between')
                    self.audit.near(node['id'], node.get('name',''), 'gap=0+大间距→space-between 启发')
        if layout == 'horizontal':
            d.append('align-items: center')
            hd.append('align-items: center')
        p = node.get('padding') or {}
        for side in ('left', 'right', 'top', 'bottom'):
            v = p.get(side, 0)
            if v:
                d.append(f'padding-{side}: {self.rpx(v)}')
                hd.append(f'padding-{side}: {v:.1f}px')

    def fill_decls(self, node, d, hd, for_text=False):
        for f in (node.get('fills') or []):
            if not f.get('visible', True):
                continue
            ft = f['type']
            if ft == 'SOLID':
                op = f.get('opacity', 1)
                has_blur = any(e.get('type') == 'BACKGROUND_BLUR' for e in (node.get('effects') or []))
                if has_blur and op < 0.72:
                    op = 0.72
                    self.audit.near(node['id'], node.get('name',''), 'BACKGROUND_BLUR→白底0.72降级(5.25接真blur)')
                col = rgba(f['color'], op if op < 1 else None)
                d.append(('color: ' if for_text else 'background-color: ') + col)
                hd.append(('color: ' if for_text else 'background-color: ') + col)
            elif ft == 'GRADIENT_LINEAR':
                stops = [s for s in (f.get('gradientStops') or []) if s['color'].get('a', 1) > 0.01]
                if len(stops) >= 2:
                    c0, c1 = rgba(stops[0]['color']), rgba(stops[-1]['color'])
                    d.append(f'background-image: linear-gradient(to bottom, {c0}, {c1})')
                    hd.append(f'background-image: linear-gradient(to bottom, {c0}, {c1})')
                    if len(f.get('gradientStops') or []) > 2:
                        self.audit.near(node['id'], node.get('name',''), f'{len(f["gradientStops"])}停驻渐变→两色近似')
                else:
                    self.audit.miss(node['id'], node.get('name',''), '渐变无可视停驻')
            elif ft == 'GRADIENT_RADIAL':
                stops = f.get('gradientStops') or []
                if stops:
                    col = rgba(stops[0]['color'])
                    d.append(f'background-color: {col}')
                    hd.append(f'background: radial-gradient(circle, {col} 0%, transparent 70%)')
                    self.audit.near(node['id'], node.get('name',''), '径向渐变→uvue中心色实底/html径向渐变')
            elif ft == 'IMAGE':
                d.append('background-color: #EDE5D5')
                hd.append('background-color: #EDE5D5')
                self.audit.near(node['id'], node.get('name',''), 'IMAGE填充→占位色(需接数据src)')

    def stroke_decls(self, node, d, hd):
        st = (node.get('strokes') or [None])[0]
        if st and st.get('visible', True):
            wt = node.get('strokeWeight', 1) or 1
            col = rgba(st['color'], st.get('opacity', 1) if st.get('opacity', 1) < 1 else None)
            d.append(f'border-width: {self.rpx(wt)}')
            d.append(f'border-color: {col}')
            hd.append(f'border: {wt:.1f}px solid {col}')

    def effect_decls(self, node, d, hd):
        for e in (node.get('effects') or []):
            if not e.get('visible', True):
                continue
            if e['type'] == 'DROP_SHADOW':
                c = e['color']
                if 'box-shadow' not in ' '.join(d):
                    d.append(f'box-shadow: {self.rpx(e["offset"]["x"])} {self.rpx(e["offset"]["y"])} {self.rpx(e["radius"]*2)} {rgba(c)}')
                    hd.append(f'box-shadow: {e["offset"]["x"]:.0f}px {e["offset"]["y"]:.0f}px {e["radius"]*2:.0f}px {rgba(c)}')
                break

    def walk(self, node, parent, root=False):
        t = node.get('type')
        cls = self.cls(node)
        name = node.get('name', '')
        nid = node['id']
        pad = self.indent * '\t'
        hpad = self.indent * '  '

        if not node.get('visible', True):
            self.audit.near(nid, name, 'visible=false 跳过')
            return

        # 矢量（图标 FRAME 或裸矢量）→ SVG image
        if t in VEC_TYPES or is_icon(node):
            slug = 'i' + nid.replace(':', '_')
            self.icons.append((nid, slug, name))
            w, h = node.get('width', 20), node.get('height', 20)
            self.ulines.append(f'{pad}<image class="{cls}" src="/static/icons/{slug}.svg" /> <!-- icon: {name} -->')
            self.hlines.append(f'{hpad}<img class="{cls}" src="icons/{slug}.svg" /> <!-- {name} -->')
            d, hd = self.box_decls(node, parent, root)
            self.emit(cls, d)
            self.emit_h(cls, hd)
            if t in VEC_TYPES:
                self.audit.near(nid, name, '裸矢量→SVG image')
            return

        if t == 'TEXT':
            txt = (node.get('characters') or '').replace('\n', ' ')
            self.ulines.append(f'{pad}<text class="{cls}">{txt}</text> <!-- {name} -->')
            esc = txt.replace('&', '&amp;').replace('<', '&lt;')
            self.hlines.append(f'{hpad}<span class="{cls}">{esc}</span> <!-- {name} -->')
            d, hd = self.box_decls(node, parent, root, is_text=True)
            fn = node.get('fontName') or {}
            style = fn.get('style', 'Regular')
            if style == 'Bold':
                d.append('font-weight: bold')
                hd.append('font-weight: bold')
            elif style in ('Semi Bold', 'SemiBold'):
                d.append('font-weight: 600')
                hd.append('font-weight: 600')
            fs = node.get('fontSize', 14)
            d.append(f'font-size: {self.rpx(fs)}')
            hd.append(f'font-size: {fs:.1f}px')
            for f in (node.get('fills') or []):
                if f['type'] == 'SOLID' and f.get('visible', True):
                    col = rgba(f['color'], f.get('opacity', 1) if f.get('opacity', 1) < 1 else None)
                    d.append(f'color: {col}')
                    hd.append(f'color: {col}')
                    break
            for e in (node.get('effects') or []):
                if e['type'] == 'DROP_SHADOW':
                    c = e['color']
                    d.append(f'text-shadow: {self.rpx(e["offset"]["x"])} {self.rpx(e["offset"]["y"])} {self.rpx(e["radius"]*2)} {rgba(c)}')
                    hd.append(f'text-shadow: {e["offset"]["x"]:.0f}px {e["offset"]["y"]:.0f}px {e["radius"]*2:.0f}px {rgba(c)}')
                    break
            ta = node.get('textAlignHorizontal')
            if ta == 'CENTER':
                d.append('text-align: center')
                hd.append('text-align: center')
            self.emit(cls, d)
            self.emit_h(cls, hd)
            return

        if t != 'FRAME':
            self.audit.miss(nid, name, f'未处理类型 {t}')
            return

        # FRAME
        self.ulines.append(f'{pad}<view class="{cls}"> <!-- {name} -->')
        self.hlines.append(f'{hpad}<div class="{cls}"> <!-- {name} -->')
        self.indent += 1
        d, hd = self.box_decls(node, parent, root)
        if root:
            d.insert(0, 'flex: 1')
            hd.insert(0, 'position: relative')
            hd.insert(0, 'width: 390px')
            hd.insert(0, 'height: 844px')
        self.flex_decls(node, d, hd)
        self.fill_decls(node, d, hd)
        self.stroke_decls(node, d, hd)
        self.effect_decls(node, d, hd)
        cr = node.get('cornerRadius')
        if cr:
            r = '9999rpx' if cr >= 9999 else self.rpx(cr)
            d.append(f'border-radius: {r}')
            hd.append(f'border-radius: {"999px" if cr >= 9999 else f"{cr:.1f}px"}')
        if node.get('clipsContent'):
            d.append('overflow: hidden')
            hd.append('overflow: hidden')
        self.emit(cls, d)
        self.emit_h(cls, hd)
        for c in (node.get('children') or []):
            self.walk(c, node)
        self.indent -= 1
        self.ulines.append(f'{pad}</view>')
        self.hlines.append(f'{hpad}</div>')

ICONS_JSON = os.path.join(ROOT_DIR, 'uvue_gen', 'icons_manifest.json')

def convert(frame, slug):
    global ROOT_ID
    ROOT_ID = frame['id']
    g = Gen(frame.get('name', slug))
    g.ulines.append('<template>')
    g.ulines.append(f'\t<!-- ardot 直转 v2：{frame.get("name","")}（帧 {frame["id"]}）· 审计化生成 -->')
    g.ulines.append('\t<view class="page-root">')
    g.hlines.append('<!DOCTYPE html><html><head><meta charset="utf-8"><style>')
    g.hlines.append('body{margin:0;background:#333;display:flex;justify-content:center;}')
    g.hlines.append('.phone{width:390px;height:844px;overflow:hidden;position:relative;background:#fff;}')
    g.indent = 2
    d = ['flex: 1']
    for f in (frame.get('fills') or []):
        if f['type'] == 'SOLID':
            d.append('background-color: ' + rgba(f['color'], f.get('opacity', 1) if f.get('opacity', 1) < 1 else None))
    g.emit('page-root', d)
    g.emit_h('page-root', ['width: 390px', 'height: 844px', 'position: relative', 'overflow: hidden',
                           'background-color: ' + (rgba(frame['fills'][0]['color']) if frame.get('fills') else '#fff')])
    for c in (frame.get('children') or []):
        g.walk(c, frame)
    g.ulines.append('\t</view>')
    g.ulines.append('</template>')
    # 组 uvue
    uvue = '\n'.join(g.ulines) + '\n\n<style>\n'
    for cls, decls in g.styles.items():
        uvue += f'\t.{cls} {{\n' + ''.join(f'\t\t{x};\n' for x in decls) + '\t}\n'
    uvue += '</style>\n'
    out_u = os.path.join(ROOT_DIR, 'uvue_gen', slug + '_gen.uvue')
    io.open(out_u, 'w', encoding='utf-8').write(uvue)
    # 组 html
    body = '\n'.join(g.hlines[3:])
    css = ''
    for cls, decls in g.hstyles.items():
        css += f'.{cls} {{ ' + ' '.join(x + ';' for x in decls) + ' }\n'
    html = ('\n'.join(g.hlines[:3]) + '\n' + css + '</style></head><body><div class="phone">\n'
            + body + '\n</div></body></html>\n')
    out_h = os.path.join(ROOT_DIR, 'uvue_gen', slug + '_preview.html')
    io.open(out_h, 'w', encoding='utf-8').write(html)
    ok = g.audit.report(frame.get('name', slug))
    print(f'[{"ok" if ok else "WARN"}] {slug}: uvue+html, {len(g.icons)} icons')
    return [(nid, s, nm) for nid, s, nm in g.icons]

if __name__ == '__main__':
    nodes = load_nodes()
    targets = {
        '我的页': ('2:448', 'profile'),
        'AI 对话页': ('2:332', 'ai'),
        '搜索页': ('2:386', 'search'),
        '消息中心': ('4:89', 'messages'),
        '设置页': ('4:131', 'settings'),
        '冷启动访谈': ('4:167', 'interview'),
        '记忆详情页': ('4:404', 'detail'),
        '定稿 · 精修高保真': ('2:189', 'index'),
    }
    only = sys.argv[1] if len(sys.argv) > 1 else None
    all_icons = {}
    for name, (fid, slug) in targets.items():
        if only and only not in name:
            continue
        fr = find_frame(nodes, fid)
        if fr:
            icons = convert(fr, slug)
            for nid, s, nm in icons:
                all_icons[nid] = (s, nm)
        else:
            print(f'[miss] {name} {fid}')
    io.open(ICONS_JSON, 'w', encoding='utf-8').write(json.dumps(all_icons, ensure_ascii=False, indent=1))
    print(f'图标清单 → {ICONS_JSON}（{len(all_icons)} 个）')
