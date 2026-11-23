#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成忆述光华 V2 架构图（纯手写 SVG，无外部依赖）。"""

import os

OUT = "/Users/silver/GHapp/docs/架构图"

INK = "#16324F"
INK2 = "#3C5A78"
MUTED = "#647788"
LINE = "#A9B7C5"
PANEL = "#F3F6FA"
WHITE = "#FFFFFF"
TEAL = "#2F6F7E"
TEAL_SOFT = "#EAF2F4"
WARM = "#C7763F"
WARM_SOFT = "#F8EEE7"
RED = "#A8452F"
GRAY = "#DDE4EB"

FONT = "'PingFang SC','Hiragino Sans GB','Heiti SC','Microsoft YaHei',Helvetica,Arial,sans-serif"


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class SVG:
    def __init__(self, w, h, title):
        self.w, self.h = w, h
        self.title = title
        self.parts = []

    def add(self, s):
        self.parts.append(s)

    def rect(self, x, y, w, h, fill=WHITE, stroke=LINE, sw=1, rx=10, dash=None, opacity=1):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"{d} opacity="{opacity}"/>'
        )

    def text(self, x, y, s, size=13, weight="normal", fill=INK, anchor="start", ls=0):
        self.add(
            f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" letter-spacing="{ls}">{esc(s)}</text>'
        )

    def lines(self, x, y, items, size=12, lh=19, fill=MUTED, weight="normal"):
        cy = y
        for it in items:
            self.text(x, cy, it, size, weight, fill)
            cy += lh
        return cy

    def header(self, title, sub, badge=""):
        self.text(40, 46, title, 26, "600", INK)
        self.text(40, 72, sub, 13, "normal", MUTED)
        if badge:
            w = 18 * len(badge) * 0.62 + 28
            self.rect(self.w - 40 - w, 26, w, 30, TEAL_SOFT, TEAL, 1, 15)
            self.text(self.w - 40 - w / 2, 46, badge, 12, "600", TEAL, "middle")

    def arrow(self, x1, y1, x2, y2, color=INK, dash=None, sw=1.6, label=None, lx=0, ly=0, label_size=11):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        mid = ""
        if abs(x1 - x2) > 6 and abs(y1 - y2) > 6:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            mid = f'<path d="M {x1} {y1} L {mx} {y1} L {mx} {y2} L {x2} {y2}" fill="none" stroke="{color}" stroke-width="{sw}"{d} marker-end="url(#ah-{self.marker(color)})"/>'
            self.add(mid)
        else:
            self.add(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
                f'stroke-width="{sw}"{d} marker-end="url(#ah-{self.marker(color)})"/>'
            )
        if label:
            tx, ty = (x1 + x2) / 2 + lx, (y1 + y2) / 2 + ly
            self.text(tx, ty, label, label_size, "normal", color, "middle")

    @staticmethod
    def marker(color):
        return {INK: "ink", TEAL: "teal", WARM: "warm", LINE: "gray", RED: "red"}.get(color, "ink")

    def save(self, name):
        defs = "".join(
            f'<marker id="ah-{mid}" markerWidth="9" markerHeight="9" refX="7.5" refY="4" orient="auto">'
            f'<path d="M0,0 L8,4 L0,8 z" fill="{c}"/></marker>'
            for c, mid in [(INK, "ink"), (TEAL, "teal"), (WARM, "warm"), (LINE, "gray"), (RED, "red")]
        )
        body = "\n".join(self.parts)
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}">\n'
            f'<defs>{defs}</defs>\n'
            f'<rect width="{self.w}" height="{self.h}" fill="#FFFFFF"/>\n{body}\n</svg>\n'
        )
        path = os.path.join(OUT, name + ".svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)

        # 供 qlmanage 生成方形缩略图用（避免非方形被裁切）
        side = max(self.w, self.h)
        pad_x, pad_y = (side - self.w) / 2, (side - self.h) / 2
        svg_pad = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{side}" height="{side}" '
            f'viewBox="0 0 {side} {side}">\n'
            f'<defs>{defs}</defs>\n'
            f'<rect width="{side}" height="{side}" fill="#FFFFFF"/>\n'
            f'<g transform="translate({pad_x},{pad_y})">\n{body}\n</g>\n</svg>\n'
        )
        with open(os.path.join(OUT, name + ".pad.svg"), "w", encoding="utf-8") as f:
            f.write(svg_pad)
        return path


def card(s, x, y, w, h, title, body, accent=TEAL, fill=WHITE, tag=None, title_size=14, body_size=12):
    s.rect(x, y, w, h, fill, LINE, 1)
    s.rect(x, y, 4, h, accent, accent, 0, rx=2)
    s.text(x + 14, y + 24, title, title_size, "600", INK)
    cy = y + 46
    for b in body:
        s.text(x + 14, cy, b, body_size, "normal", MUTED)
        cy += body_size + 6
    if tag:
        tw = 10.5 * len(tag) * 0.72 + 14
        if w >= 220:
            s.rect(x + w - tw - 10, y + 8, tw, 19, PANEL, LINE, 1, 9)
            s.text(x + w - tw / 2 - 10, y + 21.5, tag, 10, "600", MUTED, "middle")
        else:
            s.rect(x + 14, y + h - 25, tw, 18, PANEL, LINE, 1, 9)
            s.text(x + 14 + tw / 2, y + h - 12.5, tag, 9.5, "600", MUTED, "middle")


# ---------------------------------------------------------------- 图 1 总图
def d1():
    W, H = 1780, 1400
    s = SVG(W, H, "总图")
    s.header("忆述光华 · 三合一陪伴智能体 V2｜总图",
             "六域一核 · 七层 · 依赖单向（L6 → L5 → L4 → 核心 → L2 → L1 → L0）",
             "V2 · 2026-11-20")

    # L6
    y0 = 96
    s.rect(40, y0, W - 80, 132, PANEL, GRAY, 1)
    s.text(60, y0 + 26, "L6  端侧（uni-app x + UTS 原生）· 一套代码四种运行形态", 14, "600", INK)
    forms = [("本人端", "默认：陪伴 + 检索 + 时间轴", TEAL),
             ("子女端", "代建：批量导入 + 老人授权", INK2),
             ("老人端", "适老 token 档：字大、语音优先", WARM),
             ("妈妈端", "成长线 + 家人权限 + 交接", INK2)]
    cw = (W - 80 - 40 - 3 * 16) / 4
    for i, (t, d, c) in enumerate(forms):
        x = 60 + i * (cw + 16)
        card(s, x, y0 + 40, cw, 78, t, [d], c)

    # L5
    y1 = y0 + 132 + 22
    s.rect(40, y1, W - 80, 82, WHITE, GRAY, 1)
    s.text(60, y1 + 26, "L5  接入层", 14, "600", INK)
    s.text(60, y1 + 52, "API 网关：鉴权 · 限流 · 幂等键 · 参数归一 · 结果剪裁", 12, "normal", MUTED)
    gw = 300
    for i, t in enumerate(["用户端 REST（JWT）", "Agent 端点（X-Agent-Token）", "微信回调（验签）"]):
        x = W - 60 - (3 - i) * (gw + 12)
        s.rect(x, y1 + 26, gw, 40, PANEL, LINE, 1, 8)
        s.text(x + gw / 2, y1 + 51, t, 12, "500", INK2, "middle")

    # L4
    y2 = y1 + 82 + 22
    s.rect(40, y2, W - 80, 108, TEAL_SOFT, TEAL, 1)
    s.text(60, y2 + 28, "L4  编排层 Orchestrator（唯一大脑 · 无状态）", 14, "600", INK)
    s.text(60, y2 + 50, "只选路不代干；并行拉起；预算控制；失败必须落三态", 12, "normal", TEAL)
    chips = ["① RP 陪伴", "② 检索回忆", "③ 摄入/整理", "④ 回响/推送", "⑤ 设置与信任", "⑥ 混合引用私料"]
    cwid = (W - 100 - 5 * 14) / 6
    for i, t in enumerate(chips):
        x = 60 + i * (cwid + 14)
        s.rect(x, y2 + 62, cwid, 34, WHITE, TEAL, 1, 8)
        s.text(x + cwid / 2, y2 + 84, t, 12, "600", INK, "middle")

    # 核心 A / B
    y3 = y2 + 108 + 30
    ah = 452
    ax, aw = 60, 470
    bx, bw = 560, W - 80 - 560

    s.rect(ax, y3, aw, ah, WARM_SOFT, WARM, 1)
    s.text(ax + 20, y3 + 28, "核心 A｜RP 陪伴 Harness", 15, "600", INK)
    s.text(ax + 20, y3 + 50, "旁挂 Agent 进程（不在层里）· LangGraph", 11.5, "normal", WARM)
    agents = [("Muse", "主生成 · 必跑"), ("Librarian", "检索 · 合并调用"),
              ("Serendipity", "预取 · 异步"), ("Provenance", "校验 · 触发式"),
              ("Sculptor", "整理 · 夜间闲时")]
    for i, (n, d) in enumerate(agents):
        row, col = divmod(i, 2)
        x = ax + 18 + col * ((aw - 36 - 12) / 2 + 12)
        cw2 = (aw - 36 - 12) / 2
        card(s, x, y3 + 70 + row * 62, cw2, 52, n, [d], WARM, title_size=12.5)
    s.text(ax + 20, y3 + 320, "人设卡（版本快照）· 状态机（熟悉度/基调/故事线/关系）", 11.5, "normal", INK2)
    s.text(ax + 20, y3 + 342, "上下文帧九段 · 输出闸 · checkpointer（执行态）", 11.5, "normal", INK2)
    s.rect(ax + 18, y3 + 360, aw - 36, 72, WHITE, LINE, 1, 8)
    s.text(ax + 32, y3 + 382, "三条红线", 11.5, "600", RED)
    s.text(ax + 32, y3 + 402, "不持 COS/Qdrant 凭据 · 不碰 crisis/12356 · 不写业务表", 10.5, "normal", MUTED)
    s.text(ax + 32, y3 + 420, "模型只有提案权，写权在 App", 10.5, "normal", MUTED)

    s.rect(bx, y3, bw, ah, WHITE, TEAL, 1)
    s.text(bx + 20, y3 + 28, "核心 B｜回忆资产内核（backend B 域）", 15, "600", INK)
    s.text(bx + 20, y3 + 50, "六个业务域 + 三个横切域；纯策略下沉 L1，入口适配在 L3", 11.5, "normal", TEAL)

    doms = [
        ("B1 摄入 Ingest", ["批量导入 / 语音速记 / 微信转发", "L0→L3 分级 · 折叠不删"], "需求 1-7,27"),
        ("B2 事件 Event", ["四层聚合 · 事件卡 · 故事线", "人工拆并永不被覆盖"], "需求 3,13,18,23"),
        ("B3 检索 Retrieval", ["单次归一 · 三通道一致", "三级降级 · trace · clarify"], "需求 8-17"),
        ("B4 回响 Echo", ["资格计算 · 雷区 · 静默", "成长对比 · 分享 · 回忆册"], "需求 19-25"),
        ("B5 记忆 Memory", ["四层记忆 · 双时序事实", "投影 600 字 · 修正优先"], "需求 33,38,40"),
        ("B6 信任 Trust", ["用途授权 · 保险箱", "删除生命周期 · 导出导入"], "需求 26-34"),
        ("B7 有人味 Human", ["共情三轴 · 性格 5 维", "情绪契约 · 危机三级"], "需求 35-40"),
        ("B8 家庭 Family", ["家庭组 / 权限 / 交接", "老人端 · 问答引导 · 术语"], "需求 41-45"),
        ("B9 评测 Eval", ["评测集 / 难负例", "CI 闸门 / 降级开关"], "需求 17,45"),
    ]
    inner_x = bx + 20
    inner_w = bw - 40
    gap = 14
    cw3 = (inner_w - 2 * gap) / 3
    for i, (t, body, tag) in enumerate(doms):
        r, c = divmod(i, 3)
        x = inner_x + c * (cw3 + gap)
        y = y3 + 70 + r * 128
        card(s, x, y, cw3, 116, t, body, TEAL, tag=tag, title_size=12.5)

    # L2
    y4 = y3 + ah + 30
    s.rect(40, y4, W - 80, 116, PANEL, GRAY, 1)
    s.text(60, y4 + 26, "L2  端口层 Protocol（失败必须三态以上）", 14, "600", INK)
    ports = ["SearchPort", "TimelinePort", "IngestPort", "EchoPort", "MemoryPort", "ConsentPort",
             "VaultPort", "LifecyclePort", "ExportPort", "ProfileDigestPort", "GuardPort",
             "CrisisPort", "PersonaPort", "FamilyPort", "EvalPort"]
    pw = 196
    per_row = 8
    for i, p in enumerate(ports):
        r, c = divmod(i, per_row)
        x = 60 + c * (pw + 10)
        y = y4 + 42 + r * 34
        s.rect(x, y, pw, 28, WHITE, LINE, 1, 7)
        s.text(x + pw / 2, y + 19, p, 11, "500", INK2, "middle")

    # L1
    y5 = y4 + 116 + 20
    s.rect(40, y5, W - 80, 90, WHITE, GRAY, 1)
    s.text(60, y5 + 26, "L1  域内核（纯策略：不 import FastAPI / SQLAlchemy / LangGraph）", 13.5, "600", INK)
    cores = ["rag/resolve", "rag/fuse", "event_aggregation", "echo/rules",
             "llm_ops/guard", "crisis/scan", "profile_*/interview"]
    for i, t in enumerate(cores):
        x = 60 + i * 232
        s.rect(x, y5 + 42, 220, 30, PANEL, LINE, 1, 7)
        s.text(x + 110, y5 + 62, t, 11, "500", INK2, "middle")

    # L0
    y6 = y5 + 90 + 20
    s.rect(40, y6, W - 80, 130, WHITE, GRAY, 1)
    s.text(60, y6 + 26, "L0  基础设施（可换）", 13.5, "600", INK)
    inf = ["PostgreSQL 28+12 表", "Qdrant（named vectors）", "COS（STS 直传）", "Redis + RQ 多优先级",
           "百炼 LLM / Qwen3-VL", "FunASR / SenseVoice", "企业微信客服", "JobScheduler 三条件", "Sentry 双通道"]
    for i, t in enumerate(inf):
        x = 60 + i * 185
        s.rect(x, y6 + 44, 172, 32, PANEL, LINE, 1, 7)
        s.text(x + 86, y6 + 65, t, 10.5, "500", INK2, "middle")
    s.text(60, y6 + 104, "JobScheduler 三条件：插电 + 非计量网络 + 空闲（电量 > 20%）", 11, "normal", MUTED)

    # 主轴箭头
    mx = W / 2
    s.arrow(mx, y0 + 132, mx, y1, INK)
    s.arrow(mx, y1 + 82, mx, y2, INK)
    s.arrow(ax + aw / 2, y2 + 108, ax + aw / 2, y3, WARM)
    s.arrow(bx + bw / 2, y2 + 108, bx + bw / 2, y3, TEAL)
    s.arrow(ax + aw / 2, y3 + ah, ax + aw / 2, y4, WARM, dash="5 4")
    s.arrow(bx + bw / 2, y3 + ah, bx + bw / 2, y4, TEAL, dash="5 4")
    s.text(ax + aw / 2 + 8, y3 + ah + 20, "只经端口读", 10.5, "normal", WARM)
    s.arrow(mx, y4 + 116, mx, y5, INK)
    s.arrow(mx, y5 + 90, mx, y6, INK)
    return s.save("01-总图")


# ---------------------------------------------------------------- 图 2 请求链路
def d2():
    W, H = 1300, 1580
    s = SVG(W, H, "请求链路")
    s.header("一次请求的完整链路（混合场景：陪聊中引用你自己的回忆）",
             "左列为主链路，右列为耗时量级；虚线部分全部异步，不占用户等待时间", "链路图")

    L = 70      # 主列 x
    BW = 720    # 主列框宽
    AX = 830    # 右侧注释起点

    steps = []

    def step(y, h, title, body, accent=INK, tag=None):
        card(s, L, y, BW, h, title, body, accent, tag=tag, title_size=13.5)

    y = 110
    step(y, 66, "① 用户输入", ["文字 / 语音（ASR）/ 照片（以图搜）/ 时间轴点选"], INK2)
    s.arrow(L + BW / 2, y + 66, L + BW / 2, y + 96, INK)
    y += 96

    step(y, 74, "② 输入护栏（前置）", ["越狱/注入 → 拦截并按人设回应；危机词 → 转 crisis 链路（不进 Agent）"], RED)
    s.text(AX, y + 40, "< 10 ms", 11.5, "600", MUTED)
    s.arrow(L + BW / 2, y + 74, L + BW / 2, y + 104, INK)
    y += 104

    step(y, 96, "③ 意图路由（6 类）", ["RP 陪伴 / 检索回忆 / 摄入整理 / 回响推送 / 设置信任 / 混合引用私料",
                                  "只决定“选谁干”，不做业务判断"], TEAL)
    s.text(AX, y + 46, "50–100 ms", 11.5, "600", MUTED)
    s.arrow(L + BW / 2, y + 96, L + BW / 2, y + 126, INK)
    y += 126

    # 并行块：五路取料（与图 03 的五个入口严格一致）
    s.rect(L, y, BW, 214, PANEL, LINE, 1)
    s.text(L + 16, y + 24, "④ 取料：五路并行（彼此独立，缺一路不阻塞）", 13, "600", INK)
    sub = [("Search", ["事件级检索", "top-k+rerank"], TEAL, "必须"),
           ("Timeline", ["时间轴上下文", "接续讲述用"], INK2, "按需"),
           ("Person", ["按人过滤", "content_persons"], INK2, "按需"),
           ("Digest", ["投影≤600字", "三态"], WARM, "必须"),
           ("Memory", ["叙事记忆片段", "top-k+时间近"], INK2, "按需")]
    sw = (BW - 30 - 4 * 8) / 5
    for i, (t, b, c, tag) in enumerate(sub):
        x = L + 14 + i * (sw + 8)
        card(s, x, y + 34, sw, 106, t, b, c, tag=tag, title_size=11.5, body_size=10.5)
    s.text(L + 16, y + 156, "必须 = 本轮涉及回忆或涉人时的下限；按需 = 只在接续讲述 / 续聊时才取", 11, "normal", MUTED)
    s.text(L + 16, y + 178, "五路全部只读，任何一路超时都只降级、不阻断本轮生成", 11, "normal", MUTED)
    s.text(AX, y + 90, "100–300 ms", 11.5, "600", MUTED)
    s.text(AX, y + 108, "（并行）", 10.5, "normal", MUTED)
    s.arrow(L + BW / 2, y + 214, L + BW / 2, y + 244, INK)
    y += 244

    step(y, 104, "⑤ 上下文帧组装（九段 · 带预算）", ["人设 / 状态 / 画像投影 / 叙事记忆 / 参考资料（只读+溯源）",
                                                "最近对话 / 本轮输入 / 输出要求（含 state 块规范）",
                                                "超预算：先砍第 8 段情绪；第 1 段称呼与第 6 段回避话题永不砍"], INK)
    s.text(AX, y + 48, "< 20 ms", 11.5, "600", MUTED)
    s.arrow(L + BW / 2, y + 104, L + BW / 2, y + 134, INK)
    y += 134

    step(y, 88, "⑥ 主生成（流式）", ["Muse 出回复 + state 块提案；人设硬约束优先于参考资料",
                                 "参考块为空 → “我这边没翻到”，禁止虚构"], WARM, tag="最大耗时")
    s.text(AX, y + 40, "1–4 s", 11.5, "600", MUTED)
    s.text(AX, y + 58, "取决于模型档", 10.5, "normal", MUTED)
    s.arrow(L + BW / 2, y + 88, L + BW / 2, y + 118, WARM)
    y += 118

    # 三道闸
    s.rect(L, y, BW, 176, WARM_SOFT, WARM, 1)
    s.text(L + 16, y + 24, "⑦ 输出闸（三检 · 命中一次重试 → 再降级）", 13, "600", INK)
    gates = [("溯源校验", ["引用私料必须有证据", "无证据：fail-closed"], RED),
             ("共情校验", ["禁语清单（“别想太多”）", "fail-open，不阻断回复"], WARM),
             ("OOC 校验", ["是否变成客服 / 脱离人设", "命中 → 重试一次"], INK2)]
    gw = (BW - 32 - 2 * 12) / 3
    for i, (t, b, c) in enumerate(gates):
        x = L + 16 + i * (gw + 12)
        card(s, x, y + 36, gw, 92, t, b, c, title_size=12)
    s.text(L + 16, y + 152, "重试仍不通过 → 短回应 + 不追问（宁可少说，不许假装）", 11, "normal", MUTED)
    s.text(AX, y + 90, "≈ 50 ms", 11.5, "600", MUTED)
    s.arrow(L + BW / 2, y + 176, L + BW / 2, y + 206, INK)
    y += 206

    step(y, 96, "⑧ 状态提案解析 + 三态落库", ["state 块解析失败 → 只降级不重试整轮（重试会让人听出机器味）",
                                           "写回三态：confirmed（200）/ draft（超时/无 token）/ rejected（护栏拒收）"], TEAL)
    s.arrow(L + BW / 2, y + 96, L + BW / 2, y + 126, INK)
    y += 126

    step(y, 60, "⑨ 返回用户", ["流式逐字返回；卡片渲染事件卡而非文件列表"], INK2)

    # 异步块
    ay = y + 90
    s.rect(L, ay, BW, 200, PANEL, LINE, 1, dash="6 4")
    s.text(L + 16, ay + 26, "⑩ 异步（运行在 P2/P3 队列，绝不阻塞上一轮）", 13, "600", MUTED)
    asyncs = [("Sculptor 记忆整理", "夜间闲时批处理，只加摘要层，不改原文"),
              ("Serendipity 预取", "话题漂移时预取，失败静默放弃"),
              ("向量化 / 事件聚合", "L2 文本、L3 图像向量、event_key 归并"),
              ("Sculptor 生成画像投影", "8 段重算，写回后下一轮生效")]
    for i, (t, b) in enumerate(asyncs):
        r, c = divmod(i, 2)
        x = L + 16 + c * ((BW - 32 - 12) / 2 + 12)
        card(s, x, ay + 40 + r * 76, (BW - 32 - 12) / 2, 66, t, [b], MUTED, title_size=11.5)
    s.text(AX, ay + 100, "不占等待时间", 11.5, "600", MUTED)

    # 右侧总耗时框
    s.rect(AX - 10, 110, 400, 120, WHITE, LINE, 1)
    s.text(AX + 6, 136, "用户感知耗时", 12.5, "600", INK)
    s.text(AX + 6, 160, "护栏 10 + 路由 80 + 召回 300", 11.5, "normal", MUTED)
    s.text(AX + 6, 180, "+ 生成 1500 ≈ 1.9 s（首字更早）", 11.5, "normal", MUTED)
    s.text(AX + 6, 204, "流式首字：召回完成即可开吐", 11, "600", TEAL)
    return s.save("02-请求链路")


# ---------------------------------------------------------------- 图 3 RP Harness
def d3():
    W, H = 1600, 900
    s = SVG(W, H, "五Agent分工")
    s.header("核心 A｜五个 Agent 怎么分工（谁干什么、谁不能干什么）",
             "五 Agent 彼此不直连，全部经应用层转发、鉴权、预算、回执", "图 03")

    # 仲裁条
    s.rect(40, 100, 1520, 70, TEAL_SOFT, TEAL, 1)
    s.text(60, 128, "本轮请求", 12.5, "600", INK)
    s.text(60, 150, "文字 / 语音 / 图片", 10.5, "normal", MUTED)
    s.arrow(200, 135, 250, 135, INK)
    s.text(262, 128, "应用层 Orchestrator（唯一仲裁者）", 12.5, "600", INK)
    s.text(262, 150, "鉴权 · 预算 · 转发 · 回执 · 超时降级", 11, "normal", TEAL)
    s.text(1230, 130, "Agent 之间禁止互相调用", 11.5, "600", RED)
    s.text(1230, 150, "（否则权限与成本失控）", 10.5, "normal", MUTED)

    # 主链路四段（每个 Agent 一个卡片，写清触发/读/出/失败）
    y = 210
    h = 205
    cols = [
        (40, 330, "① Librarian｜检索", TEAL,
         ["触发：检索回忆 / 混合引用", "读：SearchPort + TimelinePort",
          "归一由 resolve_query 唯一完成", "出：EventCard[] + trace", "失败：零命中交降级链"]),
        (400, 400, "② Muse｜主生成", WARM,
         ["触发：每一轮（唯一必跑）", "读：上下文帧九段（只读）",
          "不读：原始文件 / 人脸结论", "出：回复 + 引用标记 + state 提案", "失败：模板化安全回复"]),
        (830, 350, "③ Provenance｜校验", RED,
         ["触发：引用私料 / 敏感 / 纠错 / 危机词", "读：草稿 + 证据集 + 禁语表",
          "只判不写、不改稿", "出：pass / retry / block + 依据", "失败：私料场景 fail-closed"]),
        (1210, 350, "④ MemoryWrite｜落库", INK2,
         ["不是 Agent，是端口", "三态：confirmed / draft / rejected",
          "超时恒落 draft", "模型无直写权限", "失败可重放（幂等键）"]),
    ]
    for x, w, t, c, body in cols:
        s.rect(x, y, w, h, WHITE, LINE, 1)
        s.rect(x, y, 4, h, c, c, 0, rx=2)
        s.text(x + 16, y + 30, t, 13, "600", INK)
        s.text(x + 16, y + 50, "────────────", 10, "normal", GRAY)
        cy = y + 76
        for b in body:
            s.text(x + 16, cy, b, 11.5, "normal", MUTED)
            cy += 24
        s.text(x + w - 14, y + 20, "主链路", 9.4, "600", c, "end")
    for x1, x2, c in [(370, 400, TEAL), (800, 830, WARM), (1180, 1210, RED)]:
        s.arrow(x1 + 1, y + 40, x2 - 3, y + 40, c)

    # 侧路两只（不在主链路）
    y2 = 495
    s.rect(40, y2, 560, 220, PANEL, LINE, 1, dash="6 4")
    s.text(56, y2 + 28, "⑤ Serendipity｜联想预取（异步，可不跑）", 12.5, "600", INK)
    for i, b in enumerate(["触发：空闲 / 话题漂移 / 某人久未提及",
                           "读：MemoryPort(read) + Timeline 邻域",
                           "出：候选事件 id + 关联度（给下一轮可选用）",
                           "约束：只预取不发言、不写库、不触发推送",
                           "失败：静默放弃，用户无感"]):
        s.text(56, y2 + 58 + i * 26, b, 11, "normal", MUTED)
    s.text(56, y2 + 204, "队列：P2 · 可中断 · 不占等待（读画像主题 Top5）", 10.3, "600", TEAL)

    s.rect(620, y2, 560, 220, PANEL, LINE, 1, dash="6 4")
    s.text(636, y2 + 28, "⑥ Sculptor｜离线整理（夜间批）", 12.5, "600", INK)
    for i, b in enumerate(["触发：夜间闲时批 / 累计 N 轮",
                           "读：原始对话事件全量 + 既有摘要层",
                           "出：摘要层 / 主题分组 / 画像更新提案",
                           "约束：永不改写原文，只加可回滚的新层",
                           "失败：保留原始，次日重试（幂等）"]):
        s.text(636, y2 + 58 + i * 26, b, 11, "normal", MUTED)
    s.text(636, y2 + 204, "队列：P3 · 批处理 · 只影响下一轮上下文", 10.5, "600", MUTED)

    # 右侧落库三态
    s.rect(1210, y2, 350, 220, WHITE, INK2, 1)
    s.text(1226, y2 + 28, "落库三态（MemoryWritePort）", 12, "600", INK)
    for i, (k, v) in enumerate([("confirmed", "200 已收进叙事记忆"),
                                ("draft", "超时 / 无 token，待同步"),
                                ("rejected", "护栏拒收，只在对话里")]):
        s.text(1226, y2 + 60 + i * 30, k, 11, "600", INK2)
        s.text(1226 + 86, y2 + 60 + i * 30, v, 10.5, "normal", MUTED)
    s.text(1226, y2 + 172, "超时不是 failed —— 真实终态未知", 10.5, "normal", RED)
    s.text(1226, y2 + 194, "所以只允许落 draft，等对账", 10.5, "normal", MUTED)

    # 侧路与主链的关系（文字标注，不画长虚线免歧义）
    s.text(560, y2 + 186, "→ 候选供下一轮", 10, "600", TEAL, "end")
    s.text(1140, y2 + 186, "→ 次日上下文", 10, "600", MUTED, "end")

    # 底部结论条
    s.rect(40, 738, 1520, 140, WHITE, GRAY, 1)
    s.text(60, 766, "五只 Agent 的触发档位与成本（这条决定单轮调用次数）", 12.5, "600", INK)
    rows = [("Muse", "每轮｜主力模型｜1–4 s"),
            ("Librarian", "涉回忆时｜轻量｜100–300 ms"),
            ("Serendipity", "空闲预取｜轻量｜不占等待"),
            ("Provenance", "触发式（私料/敏感/纠错/危机）｜轻量｜≈50 ms"),
            ("Sculptor", "夜间批｜中档批量｜次日生效")]
    for i, (n, d) in enumerate(rows):
        x = 60 + i * 300
        s.text(x, 796, n, 11.5, "600", INK2)
        s.text(x, 820, d, 10.5, "normal", MUTED)
    s.text(60, 856, "全量每轮跑五只 = 单轮 4 次以上 LLM 调用，不可接受；Provenance 必须触发式，这是与 Linger 原设计最大的偏离。",
           11.5, "600", RED)
    return s.save("03-五个Agent分工")


# ---------------------------------------------------------------- 图 4 摄入状态机
def d4():
    W, H = 1500, 1000
    s = SVG(W, H, "摄入分级")
    s.header("摄入｜为什么要分级：导入是长事务，不是一次写库",
             "用户在 0 秒就有东西可翻；AI 在后台逐级把「能翻」升成「能看 / 能搜 / 能图搜」", "图 04")

    # A 用户视角
    y = 104
    s.rect(40, y, W - 80, 196, WARM_SOFT, WARM, 1)
    s.text(60, y + 30, "A · 用户视角：导入开始后的四个时刻（不等 AI 也能用）", 13, "600", INK)
    marks = [("t = 0", "能翻", "时间轴立刻出现条目", TEAL),
             ("t ≈ 秒级", "能看", "缩略图出来，可点开", TEAL),
             ("t ≈ 分钟级", "能搜", "ASR / caption 完成，可被搜到", INK2),
             ("t ≈ 闲时", "能图搜", "向量入库，可拿图搜图", INK2)]
    cw = (W - 120 - 3 * 16) / 4
    for i, (t0, cap, d, c) in enumerate(marks):
        x = 60 + i * (cw + 16)
        s.rect(x, y + 50, cw, 118, WHITE, LINE, 1, 8)
        s.text(x + 16, y + 76, t0, 10.5, "600", MUTED)
        s.text(x + 16, y + 104, cap, 15, "600", c)
        s.text(x + 16, y + 132, d, 10.5, "normal", MUTED)
    s.text(60, y + 184, "没有中间态，就只能对用户说「导完了」——而那时他搜不到任何东西。这就是假状态的源头。", 11, "600", MUTED)

    # B 系统视角：四级产物
    y = 324
    s.rect(40, y, W - 80, 252, PANEL, LINE, 1)
    s.text(60, y + 30, "B · 系统视角：四级产物，每级独立完成、独立可重跑", 13, "600", INK)
    st = [("L0", "status=uploaded", "index_level=L0", "判定：行存在即可", "重跑：无"),
          ("L1", "thumbnail_meta", "缩略图落 COS", "判定：缩略图可取", "重跑：重新出图"),
          ("L2", "text 非空", "ASR + VLM caption", "判定：text 长度 > 0", "重跑：单条重识别"),
          ("L3", "image_vec", "向量入 Qdrant", "判定：向量可查", "重跑：单条重算向量")]
    cw2 = (W - 120 - 3 * 14) / 4
    for i, (lv, k1, k2, j, r) in enumerate(st):
        x = 60 + i * (cw2 + 14)
        s.rect(x, y + 52, cw2, 168, WHITE, LINE, 1, 8)
        s.text(x + 14, y + 78, lv, 14, "600", TEAL)
        s.text(x + 44, y + 78, k1, 10.5, "normal", INK2)
        s.text(x + 14, y + 102, k2, 10.5, "normal", MUTED)
        s.text(x + 14, y + 130, j, 10.5, "600", INK2)
        s.text(x + 14, y + 152, r, 10.5, "normal", MUTED)
    s.text(60, y + 236, "❌ 禁止：status=done 但 text 为空 —— 那是「导完了但搜不到」，必须退回 L1 并计入重跑队列。", 11, "600", RED)

    # C 三条理由
    y = 600
    s.rect(40, y, W - 80, 226, WHITE, TEAL, 1)
    s.text(60, y + 30, "C · 为什么不能一把梭（三条硬理由）", 13, "600", INK)
    why = [("① 长事务", ["5000 张全量 AI 处理是数小时级；", "单事务失败 = 全部重来。",
                        "分级 = 断点续传 + 只重跑坏的那一级。"]),
           ("② 等不起", ["用户第一秒就要看到东西；", "先给「能翻」才有安全感，",
                        "搜索能力后补，不阻塞使用。"]),
           ("③ 成本", ["VLM/向量全量跑最贵；", "只有被搜到、被点开的才升 L3，",
                      "闲时才跑 P2，白天不抢资源。"])]
    cw3 = (W - 120 - 2 * 16) / 3
    for i, (t, body) in enumerate(why):
        x = 60 + i * (cw3 + 16)
        s.rect(x, y + 50, cw3, 150, PANEL, LINE, 1, 8)
        s.text(x + 14, y + 76, t, 12, "600", TEAL)
        for j, b in enumerate(body):
            s.text(x + 14, y + 102 + j * 22, b, 10.5, "normal", MUTED)

    # D 假状态禁令
    y = 848
    s.rect(40, y, W - 80, 128, WARM_SOFT, WARM, 1)
    s.text(60, y + 30, "D · 由此产生的三条禁令（都可以被门禁断言）", 13, "600", INK)
    bans = ["① 单一百分比条：它无法表达「能翻不能搜」→ 必须换成三档计数",
            "② progress=100% 但队列仍有积压 → 进度按「产物就绪」算，不按「上传完成」算",
            "③ 折叠写成删除：折叠只是视图层，原件必须还能展开"]
    for i, b in enumerate(bans):
        s.text(64, y + 60 + i * 26, b, 11.5, "normal", INK2)
    return s.save("04-摄入分级")


# ---------------------------------------------------------------- 图 5 删除生命周期
def d5():
    W, H = 1480, 760
    s = SVG(W, H, "删除生命周期")
    s.header("信任域｜删除生命周期（从“请求”到“真删”的四层清理）",
             "用户看到的是回执与状态；我们内部必须能证明四层都清了", "需求 30")

    y = 150
    s.text(50, y - 18, "状态机", 12.5, "600", INK)
    nodes = [("请求删除", ["DELETE /users/me", "鉴权 + 二次确认"], INK2),
             ("202 回执", ["“30 天后物理删除”", "登录态立即失效"], TEAL),
             ("deleting", ["users.status='deleting'", "30 天内可撤销"], WARM),
             ("purge 执行", ["定时任务，非 dry-run", "四层依次清"], RED),
             ("done / failed", ["purge_status 真值", "失败进 /health 红灯"], INK2)]
    bw = 250
    gap = 35
    for i, (t, b, c) in enumerate(nodes):
        x = 50 + i * (bw + gap)
        card(s, x, y, bw, 120, t, b, c, title_size=13)
        if i < len(nodes) - 1:
            s.arrow(x + bw, y + 60, x + bw + gap - 4, y + 60, c)

    # 四层
    y2 = 330
    s.rect(50, y2, W - 100, 210, WHITE, LINE, 1)
    s.text(66, y2 + 28, "清理四层（顺序不可乱：先提交 DB 硬删，再清外部索引与对象）", 13, "600", INK)
    layers = [("① PostgreSQL", ["业务表硬删", "提交后才算数"]),
              ("② Qdrant", ["按 user_id 删点", "含所有 named vectors"]),
              ("③ COS", ["原图 / 缩略图 / 语音", "直链一并失效"]),
              ("④ memory schema", ["对话与执行态", "否则“删了但模型还记得”"])]
    for i, (t, b) in enumerate(layers):
        lx = 66 + i * ((W - 132 - 3 * 16) / 4 + 16)
        card(s, lx, y2 + 48, (W - 132 - 3 * 16) / 4, 120, t, b, RED, title_size=12)
    s.text(66, y2 + 196, "导出必须排除 status='deleting' 的用户；purge 失败必须可重跑且可计数", 11, "normal", MUTED)

    # 禁用的假状态
    y3 = 570
    s.rect(50, y3, W - 100, 150, WARM_SOFT, WARM, 1)
    s.text(66, y3 + 30, "四条禁止出现的假状态", 13, "600", RED)
    bad = ["① cleanup_job --dry-run 挂在生产：等于“说删了但没删”",
           "② purge_status 出现 pending：永远不知道到底删没删",
           "③ 回执说“已删除”但 Qdrant/COS 仍有残留（必须四层验）",
           "④ 删除后仍能被检索到（缓存与向量必须先失效）"]
    for i, b in enumerate(bad):
        r, c = divmod(i, 2)
        s.text(70 + c * 700, y3 + 62 + r * 34, b, 11.5, "normal", INK2)
    return s.save("05-删除生命周期")


# ---------------------------------------------------------------- 图 6 检索降级链
def d6():
    W, H = 1500, 940
    s = SVG(W, H, "检索降级链")
    s.header("检索域｜一次归一 + 三级降级（永不返回冷空结果）",
             "三条通道必须吃同一份 filters；degraded 时禁止反问", "需求 8-15")

    # 归一
    y = 140
    s.rect(50, y, 400, 96, PANEL, LINE, 1)
    s.text(66, y + 28, "SearchQuery（9+2 字段）", 13, "600", INK)
    s.text(66, y + 52, "q / limit / offset / intent / types", 10.5, "normal", MUTED)
    s.text(66, y + 70, "time / place / tag / person_id / mode", 10.5, "normal", MUTED)

    s.arrow(450, y + 48, 500, y + 48, INK)
    s.rect(500, y - 6, 330, 108, TEAL_SOFT, TEAL, 1)
    s.text(516, y + 24, "resolve_query() 唯一一次归一", 12.5, "600", INK)
    s.text(516, y + 48, "FACETS 注册表驱动", 11, "normal", TEAL)
    s.text(516, y + 68, "加一个分面只改一处", 10.5, "normal", MUTED)

    s.arrow(830, y + 48, 880, y + 48, TEAL)
    s.rect(880, y - 22, 570, 156, WHITE, LINE, 1)
    s.text(896, y + 6, "ResolvedQuery{filters, channels, degrade_plan}", 12, "600", INK)
    ch = [("Qdrant 通道", "vector + payload filter"),
          ("PG 回退通道", "pg_fallback，同 filters"),
          ("PG 精确通道", "关键词/精确，同 filters")]
    for i, (t, b) in enumerate(ch):
        cx = 896 + i * 184
        s.rect(cx, y + 26, 172, 86, PANEL, LINE, 1, 8)
        s.text(cx + 12, y + 50, t, 11, "600", INK2)
        s.text(cx + 12, y + 70, b, 10, "normal", MUTED)
        s.text(cx + 12, y + 98, "filters 键集必须相同", 9.5, "600", RED)

    # 降级链
    y2 = 340
    s.text(50, y2 - 16, "三级降级（逐级放宽，每级都告诉用户发生了什么）", 12.5, "600", INK)
    chain = [("① AI 语义检索", ["dense + sparse + rerank", "事件级聚合，top-k"], TEAL, "命中即返回"),
             ("② 弱命中 → relaxed", ["自动放宽某分面", "“已去掉分类，找到 3 条”"], TEAL, "诚实告知"),
             ("③ 关键词通道", ["mode=keyword", "全文索引，零语义"], INK2, "兜底"),
             ("④ clarify / 翻时间轴", ["选项来自真实 facet 值", "给月份级落点"], WARM, "绝不给空")]
    cw = 320
    gap = 40
    for i, (t, b, c, tag) in enumerate(chain):
        x = 50 + i * (cw + gap)
        card(s, x, y2, cw, 116, t, b, c, tag=tag, title_size=12.5)
        if i < len(chain) - 1:
            s.arrow(x + cw, y2 + 58, x + cw + gap - 4, y2 + 58, c)

    s.text(50, y2 + 148, "降级规则：degraded=true（系统挂/超时）时，禁止走 ④——那是把“我坏了”说成“你再说清楚点”。", 11.5, "600", RED)
    s.text(50, y2 + 172, "clarify 最多 2 问；选项必须是库里真实存在的人名/地点/月份，禁止 LLM 生成。", 11, "normal", MUTED)

    # 输出与溯源
    y3 = 610
    s.rect(50, y3, W - 100, 280, WHITE, LINE, 1)
    s.text(66, y3 + 28, "返回结构（事件卡 + 溯源，一次性解释清楚）", 13, "600", INK)
    out = [("hits[]", ["事件卡，不是文件列表", "点开看当天全部素材"]),
           ("reason", ["ok / no_match", "filtered_out / not_indexed"]),
           ("relaxed[]", ["放宽了哪些条件", "用户可回退"]),
           ("event_hit_count", ["同事件去重计数", "避免一张卡变 30 条"]),
           ("trace", ["命中分面 + 各通道得分", "“红衣服 + 海边”"]),
           ("clarify", ["question + options", "degraded 时恒为 null"])]
    for i, (t, b) in enumerate(out):
        cw2 = (W - 132 - 2 * 14) / 3
        r, c = divmod(i, 3)
        x = 66 + c * (cw2 + 14)
        y = y3 + 46 + r * 106
        card(s, x, y, cw2, 94, t, b, TEAL, title_size=11.5)
    return s.save("06-检索降级链")



# ---------------------------------------------------------------- 图 7 Linger 对比
def d7():
    W, H = 1600, 880
    s = SVG(W, H, "Linger对比")
    s.header("五 Agent 的 Harness：Linger 的范式与我们落地后的偏离",
             "左：公开描述的 Linger 结构｜中：我们完全照搬的部分｜右：我们刻意改掉的五处", "图 07")

    # 左：Linger
    lx, lw = 40, 520
    s.rect(lx, 108, lw, 660, PANEL, LINE, 1)
    s.text(lx + 16, 136, "Linger（LoCoMo 95% 公开描述的范式）", 12.5, "600", INK)
    s.text(lx + 16, 158, "复述性质，细节以其仓库/论文为准", 10.5, "normal", MUTED)

    s.rect(lx + 16, 176, lw - 32, 52, WHITE, LINE, 1, 6)
    s.text(lx + 30, 208, "用户输入（文本）", 11.5, "500", INK2)
    s.arrow(lx + lw / 2, 228, lx + lw / 2, 252, INK)

    s.rect(lx + 16, 252, lw - 32, 86, TEAL_SOFT, TEAL, 1, 6)
    s.text(lx + 30, 278, "应用层（Application Layer）", 12, "600", INK)
    s.text(lx + 30, 300, "仲裁 · 权限 · 上下文预算 · 转发", 10.5, "normal", TEAL)
    s.text(lx + 30, 320, "Agent 之间不直连", 10.5, "normal", MUTED)
    s.arrow(lx + lw / 2, 338, lx + lw / 2, 360, TEAL)

    agents5 = ["Muse｜对话主生成", "Librarian｜检索", "Serendipity｜记忆召回",
               "Provenance｜溯源校验", "Sculptor｜离线整理"]
    for i, a in enumerate(agents5):
        y = 360 + i * 44
        s.rect(lx + 16, y, lw - 32, 36, WHITE, WARM if i == 0 else LINE, 1, 6)
        s.text(lx + 30, y + 23, a, 11, "500", INK2)
    s.arrow(lx + lw / 2, 580, lx + lw / 2, 606, INK)

    s.rect(lx + 16, 606, lw - 32, 150, WHITE, LINE, 1, 6)
    s.text(lx + 30, 632, "记忆两阶段写入", 12, "600", INK)
    s.text(lx + 30, 656, "Muse 提名 → Provenance 审核 →", 10.5, "normal", MUTED)
    s.text(lx + 30, 676, "应用层统一落库（模型无写权）", 10.5, "normal", MUTED)
    s.text(lx + 30, 704, "原始记录不可覆盖；整理只加新层", 10.5, "normal", TEAL)
    s.text(lx + 30, 728, "（Sculptor 负责摘要 / 分组 / 链接）", 10.5, "normal", MUTED)

    # 中：相同
    mx, mw = 596, 400
    s.rect(mx, 108, mw, 660, WHITE, LINE, 1)
    s.text(mx + 16, 136, "我们完全照搬的四点", 12.5, "600", TEAL)
    same = [("五角色分工不变", ["谁取料、谁出话、谁预取、", "谁审、谁整理，职责不混"]),
            ("应用层仲裁", ["Agent 不直连；越权与成本", "都在这一层被挡"]),
            ("提案 → 审核 → 落库", ["模型只提案，写权在 App", "超时落 draft 不落 failed"]),
            ("原始不可覆盖", ["整理只加可回滚的新层", "人设与原文永不被改写"])]
    for i, (t, b) in enumerate(same):
        y = 172 + i * 150
        s.rect(mx + 16, y, mw - 32, 116, PANEL, LINE, 1, 6)
        s.text(mx + 30, y + 28, t, 12, "600", INK)
        for j, ln in enumerate(b):
            s.text(mx + 30, y + 56 + j * 22, ln, 10.5, "normal", MUTED)

    # 右：不同
    rx, rw = 1032, 528
    s.rect(rx, 108, rw, 660, WHITE, WARM, 1)
    s.text(rx + 16, 136, "我们刻意改掉的五处（以及为什么）", 12.5, "600", INK)
    diff = [("① Provenance 改触发式", ["Linger 每轮都审；我们只在引用私料 / 敏感 /",
                                        "纠错 / 危机词时才审，省 3 次调用。"]),
            ("② 检索不再只有一路", ["Linger 一条 Library 检索；我们要三条通道同 filters、",
                                     "三级降级、事件级返回，否则产品层搜不到。"]),
            ("③ 幻觉边界更硬", ["私料无溯源直接 block（fail-closed），",
                                "普通对话才 fail-open —— 这是产品红线不是折中。"]),
            ("④ 多出两个域", ["事件聚合（时间轴按事串）与回响调度（去年今日），",
                              "Linger 是对话型，没有这两个结构。"]),
            ("⑤ 记忆落地不引外部服务", ["抽象 MemoryPort，落地 PG + Qdrant；",
                                      "借 Graphiti 的双时序列，不引 Neo4j。"]),]
    for i, (t, b) in enumerate(diff):
        y = 166 + i * 120
        s.rect(rx + 16, y, rw - 32, 100, WARM_SOFT, LINE, 1, 6)
        s.text(rx + 30, y + 26, t, 11.5, "600", INK)
        for j, ln in enumerate(b):
            s.text(rx + 30, y + 50 + j * 21, ln, 10, "normal", MUTED)

    s.text(40, 800, "一句话：我们抄的是 Linger 的「权限结构」，改的是它的「每轮成本」与「检索深度」——前者关乎安全，后者关乎产品能不能被用。",
           11.5, "600", INK2)
    return s.save("07-Linger范式对比")


# ---------------------------------------------------------------- 图 8-12 单个 Agent 的 Harness
def agent_page(no, name, title, badge, accent, left_lines, five, out_lines, fail_lines, cost_lines, bottom):
    lx, lw = 40, 320
    mx, mw = 380, 660
    rx, rw = 1060, 340
    ly = 104

    # ---- 先量高（不再写死，卡片高度随内容伸缩）
    left_h = 30 + sum(26 if b.startswith("##") else 22 for b in left_lines) + 14
    fh = [42 + len(b) * 20 for _, b in five]
    five_h = 44 + sum(x + 10 for x in fh) + 8
    rh = [34 + len(x) * 21 + 8 for x in (out_lines, fail_lines, cost_lines)]
    right_nat = sum(rh) + 24
    maxh = max(left_h, five_h, right_nat)
    # 三列对齐到同一底线：多出来的高度分给卡间距 / 右栏块内留白
    gap5 = 10 + max(0, maxh - five_h) / len(five)
    extra_r = max(0, maxh - right_nat) / 3

    H = ly + maxh + 22 + 54 + 26
    s = SVG(1440, H, name)
    s.header(f"{no}｜{name} 的内部 Harness", title, badge)

    # 左：谁能调它
    s.rect(lx, ly, lw, maxh, PANEL, LINE, 1)
    s.text(lx + 14, ly + 26, "谁能调它 · 触发 · 读什么", 11.5, "600", INK)
    cy = ly + 54
    gap_l = 0 if maxh <= left_h else min(9, (maxh - left_h) / len(left_lines))
    for b in left_lines:
        if b.startswith("##"):
            cy += gap_l
            s.text(lx + 14, cy, b[2:], 11.5, "600", accent)
            cy += 26
        else:
            cy += gap_l
            s.text(lx + 14, cy, b, 10.6, "normal", MUTED)
            cy += 22

    # 中：五件套
    s.rect(mx, ly, mw, maxh, WHITE, LINE, 1)
    s.text(mx + 14, ly + 26, "内部 Harness 五件套", 11.5, "600", INK)
    cy = ly + 44
    for i, (t, b) in enumerate(five):
        s.rect(mx + 12, cy, mw - 24, fh[i], PANEL, LINE, 1, 5)
        s.rect(mx + 12, cy, 3, fh[i], accent, accent, 0, rx=1)
        s.text(mx + 26, cy + 22, t, 11.2, "600", accent)
        for j, ln in enumerate(b):
            s.text(mx + 26, cy + 40 + j * 20, ln, 10.2, "normal", MUTED)
        cy += fh[i] + gap5

    # 右：输出 / 失败 / 成本
    cy = ly
    heads = [("输出契约", INK, out_lines, WHITE, LINE),
             ("失败语义（三态以上）", RED, fail_lines, WHITE, LINE),
             ("成本档与队列", INK, cost_lines, WARM_SOFT, WARM)]
    for i, (h, c, lines, fill, border) in enumerate(heads):
        bh = rh[i] + extra_r
        s.rect(rx, cy, rw, bh, fill, border, 1)
        s.text(rx + 14, cy + 24, h, 11.2, "600", c)
        for j, ln in enumerate(lines):
            s.text(rx + 14, cy + 48 + j * 21, ln, 10.2, "normal", MUTED)
        cy += bh + (12 if i < 2 else 0)

    # 底部
    by = ly + maxh + 22
    s.rect(40, by, 1360, 54, PANEL, GRAY, 1)
    s.text(56, by + 24, bottom, 10.8, "normal", INK2)
    return s.save(f"{no}-{name}")


def d8():
    return agent_page(
        "08", "Muse", "主生成：唯一出话的那只，也是唯一没有写权限的那只", "必跑｜主力模型｜1–4 s", WARM,
        ["##调用者", "应用层转发；其余 Agent 不得直调",
         "##触发", "每一轮，无例外",
         "##读什么",
         "上下文帧九段（只读快照）",
         "参考事件卡（带 card_id 与溯源）",
         "##不读什么",
         "原始文件 / 人脸结论 / 他人 Agent 内部状态",
         "后台队列与运维指标"],
        [("① 角色与提示", ["人设卡版本快照（memory_companion / grandchild_proxy 等）",
                          "全局约束：禁编造、禁诊断、禁说教、禁报分数与文件名"]),
         ("② 可用工具", ["无任何写入类工具（不建表、不删、不改）",
                        "只可产「建议卡」：打开时间轴 / 按人找 / 分享入口"]),
         ("③ 内存视图", ["窗口内对话 + 画像投影（≤600 字）+ 参考块",
                        "看不到原始文件、向量、其他人格与后台状态"]),
         ("④ 约束与闸", ["参考块只读；引用必须带 card_id，否则后置校验必拦",
                        "禁语清单内联进提示，不靠模型自觉"]),
         ("⑤ 循环与预算", ["单轮生成 + state 提案；不做多步 plan、不自我反思",
                          "最多一次重试（仅由 Provenance 判定 retry 触发）"])],
        ["自然语言回复（流式）", "引用标记 card_id[]", "state 提案（tone / familiarity / arc）",
         "可选：建议卡（跳转/分享）"],
        ["超时 → 模板化安全回复（不假装思考）", "state 解析失败 → 落 draft，不重试整轮",
         "OOC / 禁语命中 → 重试一次 → 仍命中则短回应"],
        ["主力模型：1–4 s（唯一不可省）", "与召回并行：首字可在召回后立即吐出", "单轮只调一次"],
        "与 Linger 的差异：Linger 的 Muse 负责对话并提名记忆；我们保留提名，但把「写」彻底剥到 MemoryWritePort，模型连重试都无权发起。")


def d9():
    return agent_page(
        "09", "Librarian", "检索编排：只交证据，不给结论", "涉回忆时｜轻量｜100–300 ms", TEAL,
        ["##调用者", "应用层转发；Muse 不得直接检索",
         "##触发", "意图②检索回忆、⑥混合引用私料",
         "##读什么",
         "SearchPort（事件级检索）",
         "TimelinePort（接续讲述的上下文）",
         "PersonReadPort（按人过滤）",
         "##不读什么",
         "画像 evidence（隐私降级）",
         "人脸识别的原始候选"],
        [("① 角色", ["检索编排者，不是回答者；不写一句自然语言回复"]),
         ("② 工具", ["search / timeline / person 过滤 / rerank",
                    "resolve_query：filters 唯一一次归一（FACETS 注册表驱动）"]),
         ("③ 内存视图", ["近 3 轮检索缓存：同 query 不重复打库",
                        "不保留跨会话结果（避免越权复用）"]),
         ("④ 约束", ["只返回证据，不生成结论；rewrite 必须留 diff 可回看",
                    "不得静默丢分面（三条通道 filters 键集必须相同）"]),
         ("⑤ 循环与预算", ["单次检索 + 最多 2 轮放宽（记录 relaxed）",
                          "两轮仍空 → 交降级链，不自行造答案"])],
        ["EventCard[]（事件卡，非文件列表）", "trace：命中分面 + 各通道得分", "reason / relaxed[] / clarify",
         "event_hit_count（同事件去重）"],
        ["超时 → degraded=true，且禁止给 clarify", "零命中 → 关键词 → clarify / 翻时间轴",
         "通道 filters 不一致 → 门禁直接拦"],
        ["轻量模型 + 检索：100–300 ms", "与 Person / Digest 并行发", "队列：P0（前台）"],
        "与 Linger 的差异：Linger 一路检索；我们有 Qdrant / PG 回退 / PG 精确三通道，且必须吃同一份 filters——否则新分面会静默失效。")


def d10():
    return agent_page(
        "10", "Serendipity", "联想预取：只准备，不发言", "异步｜P2 队列｜不占等待", "5B8FA8",
        ["##调用者", "应用层；由空闲事件或话题漂移信号触发",
         "##触发",
         "闲时批 / 话题漂移 / 某人久未提及",
         "##读什么",
         "MemoryPort(read)：叙事记忆片段",
         "TimelinePort：邻域事件",
         "画像活跃主题 Top5",
         "##不读什么",
         "本轮未授权的话题、私密标记内容"],
        [("① 角色", ["联想 / 预取器：为「下一轮可能用到」准备候选"]),
         ("② 工具", ["topic 相似度、候选池读写、TTL 管理", "无发言工具、无推送工具"]),
         ("③ 内存视图", ["候选池：容量上限 + TTL 1 天", "只存 id 与关联度，不存原文"]),
         ("④ 约束", ["只预取不发言；不写业务库；不触发任何回响/推送",
                    "预取失败用户不可见，也不得改变本轮回答"]),
         ("⑤ 循环与预算", ["后台批处理，可被前台任务抢占中断",
                          "同一主题每日最多预取一次"])],
        ["候选事件 id[] + 关联度分数", "给 Muse：可选上下文（可不用）", "不产出用户可见文本"],
        ["静默放弃（用户无感）", "候选为空不算失败", "超时直接丢，不重试"],
        ["轻量模型；P2 队列", "可中断；夜间优先", "单用户每日预算上限"],
        "与 Linger 的差异：Linger 的 Serendipity 偏向「记忆召回」；我们把它收窄成「预取候选」，因为本产品的召回已由 Librarian 承担。")


def d11():
    return agent_page(
        "11", "Provenance", "溯源与合规审计：只判，不写，不改稿", "触发式｜轻量｜≈50 ms", RED,
        ["##调用者", "应用层；由 Muse 的输出触发，不由 Muse 调用",
         "##触发（四类，其余不跑）",
         "引用私料 / 敏感话题 / 用户纠错 / 危机词",
         "##读什么",
         "回复草稿 + 参考块 provenance",
         "禁语表 + 人设卡 + 情绪契约",
         "##不读什么",
         "原始文件字节；其他用户数据"],
        [("① 角色", ["审计员：给出 verdict 与依据，不生成任何回复文本"]),
         ("② 工具", ["证据匹配（card_id → 参考块）", "禁语/共情规则匹配", "人设一致性规则"]),
         ("③ 内存视图", ["本轮证据集（只读，不跨轮）", "不缓存历史判定结果（防陈旧豁免）"]),
         ("④ 约束", ["不得改动草稿、不得替模型补写", "每次判定必须给理由（可审计）"]),
         ("⑤ 循环与预算", ["一次判定：pass / retry / block", "retry 最多 1 次，第二次必降级"])],
        ["verdict：pass | retry | block", "violations[]（禁语/无证据/OOC）", "依据：命中的 card_id 与规则 id"],
        ["私料无证据 → block（fail-closed）", "普通共情不足 → fail-open，不阻断回复",
         "自身不可用 → 仅私料场景阻断"],
        ["轻量；≈50 ms", "只对触发轮计费，非每轮", "与生成并行不可行：必须在生成后"],
        "与 Linger 的差异：Linger 每轮都审；我们改触发式省成本，但把「私料无证据」从 warn 升为 block——这是产品红线。")


def d12():
    return agent_page(
        "12", "Sculptor", "离线整理：只加层，不改原文", "夜间批｜P3｜次日生效", "7A6A8A",
        ["##调用者", "调度器（闲时三条件满足时拉起）",
         "##触发",
         "夜间批 / 累计 N 轮未整理",
         "##读什么",
         "原始对话事件（全量，只读）",
         "既有摘要层 / 画像维度",
         "##写什么",
         "只能写「摘要 / 分组 / 链接 / 画像提案」",
         "永不写原文与参考块"],
        [("① 角色", ["离线整理者：把长对话压成可用上下文，而不是重写历史"]),
         ("② 工具", ["摘要、主题聚类、去重、画像更新提案", "无删除工具、无改写原文工具"]),
         ("③ 内存视图", ["批窗口（如 30 天）内的对话与事件", "不读其他用户、不读原始媒体"]),
         ("④ 约束", ["永不改写原文；新增层必须可回滚", "冲突不覆盖：并列保留 + 标「待确认」"]),
         ("⑤ 循环与预算", ["批处理 + 幂等键（同批重跑不产生重复摘要）",
                          "失败次日重试，不阻塞任何前台请求"])],
        ["摘要层（供下一轮上下文使用）", "主题分组 / 人物线索", "画像更新提案（App 审核后落库）"],
        ["失败 → 保留原始，次日重试", "重复执行 → 幂等，不重复产出", "画像提案被拒 → 不影响对话"],
        ["中档模型；批量", "仅夜间闲时（插电 + 非计量 + 空闲）", "单夜预算上限，超限顺延"],
        "与 Linger 的差异：Linger 的 Sculptor 做记忆合并与分组；我们额外要求「可回滚 + 幂等 + 不改原文」，因为用户资产不可逆。")




# ---------------------------------------------------------------- 图 13 事件四层
def panel(s, x, y, w, h, title, accent=INK, fill=WHITE, border=LINE):
    s.rect(x, y, w, h, fill, border, 1)
    s.text(x + 16, y + 26, title, 12.5, "600", accent)


def d13():
    W, H = 1560, 900
    s = SVG(W, H, "事件四层")
    s.header("B2 事件聚合｜四层阶梯：从 1 张照片到 1 条故事线",
             "自动只提案，用户拆/并永远优先；事件是检索单元，不是照片是单元", "图 13")

    layers = [
        ("L1 内容", "素材", "单张照片 / 单条语音 / 单条文字（contents 一行）", "自动，无聚合", "需求 1 2 3 5", INK2),
        ("L2 会话", "半自动", "同一地点 + 短时间窗的连续素材（ST-DBSCAN 空间-时间聚类）", "算法自动分组，不命名", "需求 13 18", TEAL),
        ("L3 事件", "一件事", "可命名的一件事：「那次吃火锅」——自动提案 + 用户确认/命名", "自动提案，用户确认", "需求 8 13 14 18", WARM),
        ("L4 故事线", "一段时期", "事件序列：「小宝 0-3 岁」「刚工作那两年」——按人生阶段/时间轴", "用户或时间驱动，Sculptor 提案", "需求 18 23 44", RED),
    ]
    y = 116
    for i, (name, kind, desc, auto, need, c) in enumerate(layers):
        h = 148
        s.rect(40, y, 900, h, WHITE, LINE, 1)
        s.rect(40, y, 6, h, c, c, 0, rx=2)
        s.text(64, y + 40, name, 15, "600", INK)
        s.text(160, y + 40, kind, 10.5, "600", c)
        s.text(64, y + 76, desc, 10.8, "normal", MUTED)
        s.text(64, y + 104, "聚合方式：" + auto, 10.5, "normal", INK2)
        s.text(64, y + 128, "服务于 " + need, 10.2, "600", MUTED)
        if i < 3:
            s.arrow(490, y + h, 490, y + h + 22, c)
        y += h + 22

    # 右：三条治理规则
    x2, w2 = 970, 550
    panel(s, x2, 116, w2, 330, "三条治理规则（缺一条就会退化成「自动乱merge」）")
    rules = [
        ("① 用户拆/并永远优先", ["自动聚合只提案；用户点「这不是一件事」→ 永久拆开。",
                            "算法此后不再覆盖这个决定（与需求 33 同一条铁律）。"]),
        ("② 幂等键同源", ["照片 / 语音 / 文字进事件卡必须共用同一个 event_key 生成器。",
                        "否则「文字语音进不了照片卡」的断链会反复出现。"]),
        ("③ 事件即检索单元", ["返回 EventCard（一张卡），不是 30 张照片。",
                          "这是需求 13 的落点，也是控制结果噪声的关键。"]),
    ]
    cy = 162
    for t, ls in rules:
        s.text(x2 + 16, cy, t, 11.2, "600", TEAL)
        cy += 20
        for l in ls:
            s.text(x2 + 16, cy, l, 10.2, "normal", MUTED)
            cy += 18
        cy += 12

    panel(s, x2, 470, w2, 322, "一次「拆开」的全过程（不可被算法回滚）", WARM, WARM_SOFT, WARM)
    steps = ["① 算法提案：13 张照片 + 2 条语音 → 1 个事件「周末爬山」",
             "② 用户点「这不是一件事」→ 写入 user_override=split",
             "③ 事件卡拆分：保留子卡，父卡标记 dissoloved，不删素材",
             "④ 下一次聚合只读 user_override，先判「已拆」再聚类",
             "⑤ 结果：同一批素材不会再被合回去；时间轴显示两张卡"]
    cy = 512
    for st in steps:
        s.text(x2 + 16, cy, st, 10.4, "normal", INK2)
        cy += 30
    s.text(x2 + 16, cy + 6, "红线：自动算法永不覆盖用户决定——这条写进门禁 G6。", 10.6, "600", RED)

    s.rect(40, 812, 1480, 62, PANEL, GRAY, 1)
    s.text(60, 838, "为什么四层而不是一层：L1 是「有没有」，L2 是「在哪里/什么时候」，L3 是「这是什么」，L4 是「这属于哪段人生」。",
           11, "600", INK)
    s.text(60, 860, "四层各自可独立重跑：L1 失败不影响 L3 已有卡；L3 命名错了只改卡，不动素材（原始永不被改写）。", 10.5, "normal", MUTED)
    return s.save("13-事件四层")


# ---------------------------------------------------------------- 图 14 回响
def d14():
    W, H = 1560, 940
    s = SVG(W, H, "回响资格链")
    s.header("B4 回响｜把「推不推」做成可测函数，不是 prompt 文案",
             "eligible() 六道 AND 闸；任一为假 → 不推，不解释、不补偿、不追问", "图 14")

    gates = [("within_window", "今天是否落在 anniversary_rules 窗口（去年今日 / 生日 / 节日）", TEAL),
             ("has_asset", "该日期 ±N 天内是否有资产（空窗期推 = 硬造情绪）", TEAL),
             ("NOT excluded", "人 / 宠物 / 日期段 / 词 —— SQL 下推，词复用 profile_sensitive", WARM),
             ("NOT quiet_hours", "静默时段（跨时区纯函数，用户在夜里不被叫醒）", WARM),
             ("NOT over_frequency", "7 天内不重复推（频率上限，宁少不多）", WARM),
             ("NOT dismissed", "这条内容用户已「不再显示」→ 永久不再进候选", RED)]
    y = 116
    for i, (k, d, c) in enumerate(gates):
        s.rect(40, y, 860, 62, WHITE, LINE, 1)
        s.rect(40, y, 5, 62, c, c, 0, rx=2)
        s.text(60, y + 26, k, 12, "600", INK)
        s.text(60, y + 46, d, 10.3, "normal", MUTED)
        if i < 5:
            s.arrow(470, y + 62, 470, y + 78, GRAY if i else TEAL)
        y += 78

    s.rect(40, 604, 860, 96, WARM_SOFT, WARM, 1)
    s.text(60, 632, "eligible() = 六个条件全部为真 → 才进候选池", 12, "600", INK)
    s.text(60, 656, "任何一条为假：不推、不写 push_log、不弹「今天没有回忆」——静默是默认，不是失败。", 10.5, "normal", MUTED)
    s.text(60, 682, "候选池 ≠ 推送：进池只是「可以被今天用」，真正展示还要过一次频控与排序。", 10.5, "normal", MUTED)

    x2, w2 = 940, 580
    panel(s, x2, 116, w2, 340, "六个组件（每个都能单测）")
    rows = [("资格计算", "EchoPort.eligible() / today() 纯函数，无 LLM"),
            ("雷区过滤", "echo_exclusion + profile_sensitive（人/宠物/日期段/词）"),
            ("静默时段", "in_quiet_hours() 纯函数，跨时区；用户可关"),
            ("通道", "channel 恒 in_app（需求 19：默认克制）"),
            ("文案", "禁「怀念吗 / 还记得吗」式诱导；分享是可选入口，不是 CTA"),
            ("成长 / 分享", "growth_milestone（月龄/第一次/同场景跨年）＋ share_link 与回忆册")]
    cy = 174
    for k, v in rows:
        s.text(x2 + 16, cy, k, 10.8, "600", TEAL)
        s.text(x2 + 110, cy, v, 10.2, "normal", MUTED)
        cy += 46

    panel(s, x2, 480, w2, 220, "不做假状态：推送只有一条真通道", RED, "#FDF6F4", RED)
    s.lines(x2 + 16, 540, ["内测期真实通道是 mock，但架构上仍有 push_log 与 channel 字段；",
                           "对外只承诺 in_app —— 不做「已推送」的假状态（G9 断言）。",
                           "",
                           "禁区：不写「已联络紧急联系人」；不写「已送达」；",
                           "不因未推送而改 eligible() 的返回值。"], 10.4, 20)

    s.rect(940, 720, 580, 96, PANEL, GRAY, 1)
    s.text(956, 748, "需求 19-25 的落点", 11.2, "600", INK)
    s.text(956, 772, "19 克制 / 20 屏蔽 / 21 首次雷区 / 22 不问怀念 / 23 成长 / 24 分享 / 25 低谷少推", 10.2, "normal", MUTED)
    s.text(956, 796, "七条全部落在 eligible() 的六个条件里，没有一条靠「提示词祈祷」。", 10.2, "normal", WARM)

    s.text(40, 912, "一句话：回响的对手不是「推得不够多」，是「推得不对」——所以闸门先于文案，函数先于提示词。", 11, "600", INK2)
    return s.save("14-回响资格链")


# ---------------------------------------------------------------- 图 15 记忆四层
def d15():
    W, H = 1620, 980
    s = SVG(W, H, "记忆四层")
    s.header("B5 记忆｜四层物理隔离（这是「记得住事」的地基）",
             "分层不是为了整洁，是为了让「用户改的那一句」永远压过「模型猜的那一堆」", "图 15")

    cols = [("层", 210), ("存什么", 300), ("写入者", 220), ("读取者", 160), ("存储", 340), ("保留 / 优先级", 310)]
    x0, y0 = 30, 112
    xs, cx = [], x0
    for name, w in cols:
        xs.append((cx, w)); cx += w
    s.rect(x0, y0, cx - x0, 46, PANEL, GRAY, 1)
    for (x, w), (name, _) in zip(xs, cols):
        s.text(x + 12, y0 + 30, name, 10.8, "600", INK)

    rows = [
        ("① 叙事陪伴记忆",
         ["对话事件、印象、故事线", "段落、情绪脉络"],
         ["Sculptor（离线整理）", "Muse 提案"],
         ["Muse / Librarian"], ["PG memory_episode", "+ Qdrant"],
         ["长期，可摘要压缩"], TEAL),
        ("② 用户事实记忆",
         ["称呼、家人、人生阶段、", "偏好、重要日期"],
         ["画像标注 + 用户确认"], ["ProfileDigest 投影"],
         ["PG user_profile", "+ profile_dimension_history"],
         ["长期；双时序", "（旧行不覆盖，只关闭）"], WARM),
        ("③ 资产索引",
         ["素材元数据、caption、", "向量"],
         ["摄入管线"], ["检索（Librarian）"],
         ["PG + Qdrant + COS"], ["随内容生命周期；删除即清"], INK2),
        ("④ 偏好与修正记忆",
         ["雷区、AI 性格、情绪解释", "开关、用户纠错"],
         ["用户显式操作（唯一来源）"], ["全局，先于任何生成"],
         ["PG preferences /", "correction_log / user_ai_persona"],
         ["永久，优先级最高（压过①②③）"], RED),
    ]
    y = y0 + 46
    for name, a, b, c, d, e, col in rows:
        h = 92
        s.rect(x0, y, cx - x0, h, WHITE, LINE, 1)
        s.rect(x0, y, 5, h, col, col, 0, rx=2)
        s.text(xs[0][0] + 12, y + 36, name, 11.5, "600", col)
        for i, cell in enumerate([a, b, c, d, e], start=1):
            x = xs[i][0] + 12
            for j, ln in enumerate(cell):
                s.text(x, y + 32 + j * 19, ln, 10.2, "normal", MUTED)
        y += h

    # 左下：读取顺序
    panel(s, x0, 600, 780, 190, "读的时候，顺序是反的（④ → ② → ① → ③）", RED, "#FDF6F4", RED)
    s.lines(x0 + 16, 646, ["1. 先读 ④ 偏好与修正：用户说「别提我爸」——这句必须先于任何候选生效；",
                           "2. 再读 ② 用户事实：称呼、人生阶段、重要日期（谁是「妈妈」不能靠猜）；",
                           "3. 再读 ① 叙事记忆：事件卡与故事线段落（「那次吃火锅」）；",
                           "4. 最后读 ③ 资产索引：原文只在被引用时取，且必须带 card_id。"], 10.6, 24)
    s.text(x0 + 16, 772, "四条合计装进上下文帧；画像投影硬上限 600 字——超了先砍 ①，永不砍 ④。", 10.6, "600", INK)

    # 右下：参考谁
    panel(s, 840, 600, 750, 190, "B5 参考了谁（借 schema，不引依赖）", TEAL, TEAL_SOFT, TEAL)
    s.text(856, 642, "Zep / Graphiti（94.7%，双时序）", 10.4, "600", INK2)
    s.text(1108, 642, "→ 借双时序边 valid_from / valid_to / recorded_at", 10.4, "normal", MUTED)
    s.text(856, 668, "Mem0（92.5%，事实抽取）", 10.4, "600", INK2)
    s.text(1108, 668, "→ 借「抽取-合并」，事实归一，落地自己写，不接服务", 10.4, "normal", MUTED)
    s.text(856, 694, "Linger（95%，五 Agent）", 10.4, "600", INK2)
    s.text(1108, 694, "→ 借两阶段写：模型只提案，写权在端口，原始不改", 10.4, "normal", MUTED)
    s.text(856, 724, "四层划分是我们自己的：对应「陪伴 / 事实 / 资产 / 修正」四种寿命与权限。", 10.2, "600", INK)
    s.text(856, 752, "ADR 判负：不引 Neo4j / Graphiti 服务 / Mem0 服务 / Letta / TELOS。", 10.2, "normal", RED)
    s.text(856, 774, "落地：PG 28 表 10 域 + Qdrant named vectors；MemoryPort 是端口，不是库。", 10.2, "normal", MUTED)

    s.text(30, 852, "唯一铁律：用户改过的那一句（④）永远压过模型推断的任何东西（①②③）。", 11, "600", RED)
    s.text(30, 878, "容量淘汰不碰用户资产：遗忘只由 TTL（低价值闲聊）、用户删除、合规删除触发；存不下就扩，不许「删旧的腾地方」。", 10.5, "normal", MUTED)
    s.text(30, 906, "三条对应门禁：G4（写三态）/ G5（双时序不覆盖）/ G7（备份 RPO≤24h 与四层 purge 可证）。", 10.5, "600", TEAL)
    return s.save("15-记忆四层")


# ---------------------------------------------------------------- 图 16 记忆读写
def d16():
    W, H = 1620, 1020
    s = SVG(W, H, "记忆读写")
    s.header("B5 记忆｜写进去与读出来：两阶段写 + 双时序 + 三态落地",
             "模型只提案；端口负责落；旧事实不覆盖只关闭；读的时候先读用户改的那句", "图 16")

    # ---------- 左：写入路径
    panel(s, 30, 112, 760, 420, "写入路径（一次对话里发生的事）")
    steps = [("① Muse 提案", "生成时附 state 提案：这段值不值得记、记成 episode 还是 fact", WARM),
             ("② Provenance 校验", "触发式：引用私料必须带 card_id；无证据 block；敏感走偏好层", RED),
             ("③ MemoryWritePort", "三态：confirmed(200) / draft(超时或无 token) / rejected(护栏拒收)", TEAL),
             ("④ 落两处", "叙事片段 → memory_episode + Qdrant；事实 → user_profile 新行（旧行关闭）", INK2),
             ("⑤ Sculptor 夜间", "只加摘要层 / 主题分组 / 画像提案，永不改原文，幂等可回滚", MUTED)]
    y = 152
    for i, (t, d, c) in enumerate(steps):
        s.rect(46, y, 728, 66, WHITE, LINE, 1)
        s.rect(46, y, 4, 66, c, c, 0, rx=2)
        s.text(62, y + 26, t, 10.8, "600", c)
        s.text(62, y + 50, d, 10.0, "normal", MUTED)
        if i < 4:
            s.arrow(410, y + 66, 410, y + 76, GRAY)
        y += 76

    # ---------- 右：读出来
    panel(s, 820, 112, 770, 420, "读出来：顺序 + 预算 + 三态", RED, "#FDF6F4", RED)
    s.text(846, 158, "顺序", 10.6, "600", INK)
    s.text(846, 182, "④ 偏好修正 → ② 用户事实 → ① 叙事记忆 → ③ 资产索引", 10.4, "normal", MUTED)
    s.text(846, 212, "预算", 10.6, "600", INK)
    s.text(846, 236, "整帧硬上限；画像投影 ≤600 字（8 段结构）；超预算先砍 ① 的段落，再砍 ③ 的条数，④ 一字不砍", 10.4, "normal", MUTED)
    s.text(846, 272, "ProfileDigestPort 三态（绝不空注入）", 10.6, "600", INK)
    s.text(866, 296, "ok               → 正常投影", 10.2, "normal", TEAL)
    s.text(866, 318, "insufficient_data → 明确说「还不太了解你」，不编人设", 10.2, "normal", WARM)
    s.text(866, 340, "unavailable       → 降级为通用语气，且禁止给 clarify", 10.2, "normal", MUTED)
    s.text(846, 384, "禁：把「没有画像」写成「你是一个温暖的人」（编造）", 10.2, "600", RED)
    s.text(846, 408, "禁：把 last-write-wins 用在偏好上（偏好是显式操作，不是最新闲聊）", 10.2, "600", RED)
    s.text(846, 432, "禁：把画像当事实库直接引原文（画像只放投影，原文要 card_id）", 10.2, "600", RED)
    s.text(846, 462, "画像丢 = 数月积累不可逆 → 备份 RPO ≤24h / WAL ≤5min（G7）", 10.2, "normal", MUTED)

    # ---------- 下：双时序
    panel(s, 30, 556, 970, 290, "双时序：事实变了不是「改」，是「关旧行 + 开新行」", WARM, WARM_SOFT, WARM)
    s.text(46, 600, "2024-05  用户说「想学游泳」", 10.6, "600", INK2)
    s.text(46, 622, "行 A   valid_from=2024-05   valid_to=NULL   recorded_at=2024-05   source=对话", 10.0, "normal", MUTED)
    s.text(46, 652, "2026-11  用户说「不想学了，改成跑步」", 10.6, "600", INK2)
    s.text(46, 674, "行 A   valid_to=2026-11（关闭，不删）", 10.0, "normal", MUTED)
    s.text(46, 696, "行 B   valid_from=2026-11   valid_to=NULL   recorded_at=2026-11   supersedes=行A.id", 10.0, "normal", MUTED)
    s.rect(46, 716, 470, 108, PANEL, LINE, 1)
    s.text(60, 740, "默认查询：读当前有效（valid_to IS NULL）", 10.3, "600", TEAL)
    s.text(60, 762, "→ 它现在说「在跑步」", 10.2, "normal", MUTED)
    s.rect(526, 716, 456, 108, PANEL, LINE, 1)
    s.text(540, 740, "历史问答：读当时有效", 10.3, "600", TEAL)
    s.text(540, 762, "valid_from ≤ T AND (valid_to > T OR NULL)", 10.0, "normal", MUTED)
    s.text(540, 784, "→ 能答「去年我是不是说想学游泳？」= 是", 10.0, "normal", MUTED)
    s.text(46, 838, "recorded_at 的意义：区分「事实何时成立」与「我们何时知道」——复盘时才不会把两件事混成一件。", 10.2, "normal", INK2)

    # 冲突与遗忘
    panel(s, 1020, 556, 570, 290, "冲突与遗忘（裁决顺序写死）", TEAL, TEAL_SOFT, TEAL)
    s.text(1036, 600, "冲突裁决：用户确认 > 最新 > 置信度高", 10.4, "600", INK)
    s.text(1036, 624, "三者都不满足 → 并列保留，标「待确认」，等用户点一次", 10.1, "normal", MUTED)
    s.text(1036, 646, "宁多不忘：宁可留两条矛盾的，也不静默选一条", 10.1, "normal", MUTED)
    s.text(1036, 680, "遗忘只由三件事触发：", 10.4, "600", RED)
    s.text(1036, 702, "TTL（低价值闲聊）/ 用户删除 / 合规删除", 10.1, "normal", MUTED)
    s.text(1036, 724, "不因容量淘汰；不因「模型觉得不重要」淘汰", 10.1, "normal", MUTED)
    s.text(1036, 756, "待确认项在对话里以「一件小事」出现，不弹设置页", 10.1, "normal", MUTED)
    s.text(1036, 790, "写入口三态：confirmed / draft / rejected", 10.2, "600", TEAL)
    s.text(1036, 812, "超时恒落 draft —— 超时不是 failed，真实终态未知", 10.1, "normal", RED)

    s.rect(30, 872, 1560, 112, PANEL, GRAY, 1)
    s.text(50, 902, "一句话：记忆能力不是「存得多」，是「改得准、读得对、丢得起」。", 12, "600", INK)
    s.text(50, 928, "存得多 → 分层与向量；改得准 → 两阶段写与双时序；读得对 → 固定顺序与 600 字硬预算；丢得起 → 30 天软删 + 四层 purge 可证。", 10.2, "normal", MUTED)
    s.text(50, 954, "对应门禁：G4（写三态）/ G5（双时序不覆盖）/ G7（备份与 purge 可证）。", 10.2, "600", TEAL)
    return s.save("16-记忆读写")


# ---------------------------------------------------------------- 图 17 有人味
def d17():
    W, H = 1620, 940
    s = SVG(W, H, "有人味")
    s.header("B7 有人味｜共情、性格、情绪描述、危机接管（四件互不替代的事）",
             "共情是行为约束，性格是参数，情绪描述是 schema，危机是硬停——不能混成一个 prompt", "图 17")

    panel(s, 40, 112, 500, 360, "① 共情优先（需求 35）", TEAL, TEAL_SOFT, TEAL)
    s.lines(56, 164, ["三轴：命名情绪 → 承认处境 → 不评判",
                      "禁语清单内联进提示（「别想太多」「至少你还有…」）",
                      "判定 empathy_violations()：纯函数，可单测",
                      "fail-open：判不出来也不阻断回复（宁可温和，不可审问）"], 10.4, 24)
    s.rect(56, 306, 468, 150, WHITE, LINE, 1)
    s.text(72, 334, "禁语样例（可增不可减）", 10.6, "600", INK)
    s.lines(72, 358, ["「你要往好处想」  「至少…」  「我理解你」（空转）",
                      "「别难过了」  「这没什么」  「你应该…」"], 10.2, 22)
    s.text(72, 434, "判定命中 → 不重写整句，只换这一处（保人设）。", 10.2, "normal", MUTED)

    panel(s, 570, 112, 480, 360, "② AI 性格（需求 36）", WARM, WARM_SOFT, WARM)
    s.lines(586, 164, ["5 个封闭维度（不给自由文本，避免人设漂移）：",
                      "warmth / verbosity / advice / tone / emotion_display"], 10.4, 24)
    dims = [("warmth", "亲疏：克制 ↔ 热络"), ("verbosity", "话多：一句 ↔ 一段"),
            ("advice", "建议：只陪 ↔ 会给法子"), ("tone", "语气：平实 ↔ 俏皮"),
            ("emotion_display", "情绪外显：内敛 ↔ 明显")]
    cy = 232
    for k, v in dims:
        s.text(586, cy, k, 10.5, "600", TEAL)
        s.text(706, cy, v, 10.2, "normal", MUTED)
        cy += 30
    s.text(586, 388, "拼装方式：middleware 每轮拼 → 不改进程单例 prompt", 10.4, "600", INK)
    s.text(586, 412, "（改单例 = 一个用户改性格会影响其他人，这是老 bug 的根因）", 10.2, "normal", MUTED)
    s.text(586, 440, "PersonaPort 契约：读一次、按会话生效、可回滚。", 10.2, "normal", MUTED)

    panel(s, 1070, 112, 510, 360, "③ 情绪描述与解释权（需求 39-40）", INK2)
    s.text(1086, 164, "输出 schema：{valence, arousal, cues[], basis}", 10.8, "600", INK)
    s.text(1086, 190, "契约里没有 confidence 字段 —— 客户端 UTS 编译期就删这个字段。", 10.4, "600", RED)
    s.lines(1086, 220, ["为什么不给置信度：情绪判断本来就没有真值，给数字=假装客观，",
                        "用户会拿它当结论（「你说我 87% 难过」）。"], 10.3, 22)
    s.rect(1086, 266, 478, 186, PANEL, LINE, 1)
    s.text(1102, 292, "解释权归用户（需求 40）", 10.6, "600", INK)
    s.lines(1102, 316, ["可关：emotion_display=off → 不再描述情绪",
                        "可改：用户说「我不是难过，是累」→ correction.field=emotion",
                        "可纠正：写入 correction_log，下次同类 cues 直接采信用户",
                        "纠正优先级高于模型判断（同 ④ 层逻辑）"], 10.3, 22)

    # 危机
    panel(s, 40, 496, 980, 316, "④ 危机接管（需求 37）：T1/T2/T3 三档，行为不同不是话术不同", RED, "#FDF6F4", RED)
    tiers = [("T1 关注", "表达低落但无风险信号", "注入三轴，正常对话，缩短回复、不加追问", TEAL),
             ("T2 提示", "出现风险暗示（措辞模糊）", "表留空：不填危机类型；给资源入口，继续对话", WARM),
             ("T3 硬停", "明确自伤/伤人意图", "不进 Agent：直接给热线与线下资源，本会话不再走生成", RED)]
    y = 552
    for name, when, what, c in tiers:
        s.rect(56, y, 948, 72, WHITE, LINE, 1)
        s.rect(56, y, 4, 72, c, c, 0, rx=2)
        s.text(74, y + 26, name, 11.5, "600", c)
        s.text(150, y + 26, when, 10.2, "normal", MUTED)
        s.text(74, y + 52, what, 10.4, "normal", INK2)
        y += 84
    s.text(56, 800, "铁律：不写「已联络紧急联系人」字段——联络不了。只留 escalation_note 记录真实做过的事。", 10.6, "600", RED)

    panel(s, 1040, 496, 540, 316, "三条不许做的事（都会让「有人味」变味）", MUTED)
    s.lines(1056, 544, ["① 不许把「共情」做成一句固定开场白（模板感 = 假）",
                        "② 不许用性格设置掩盖能力缺口（「我记性不好」不能当借口）",
                        "③ 不许在危机里继续扮演角色（T3 时人设让位于安全）",
                        "",
                        "反例：用户说「我妈走了」，回「我理解你的感受，想聊聊吗？」",
                        "→ 空转共情。正解：先承认、再留白、不追问细节。"], 10.4, 26)

    s.rect(40, 840, 1480, 60, PANEL, GRAY, 1)
    s.text(60, 868, "一句话：有人味 = 记得住（B5）+ 会接情绪（B7）+ 不装（无 confidence、不编人设、不写已推送）。", 11, "600", INK)
    return s.save("17-有人味")


# ---------------------------------------------------------------- 图 18 家庭权限
def d18():
    W, H = 1620, 1000
    s = SVG(W, H, "家庭权限")
    s.header("B8 家庭域｜权限模型：默认看不见，共享才看得见",
             "角色 × 范围 双维授权；老人端是同一 App 的受限形态，不是另一套系统", "图 18")

    # ---------- ① 矩阵
    s.text(40, 108, "① 角色 × 范围（默认拒绝，显式授予）", 12, "600", INK)
    grid = [["", "all（全库）", "album（相册）", "event（单个事件）"],
            ["owner（本人）", "✓ 全部", "✓", "✓"],
            ["editor（家人协作）", "× 默认不给", "✓ 指定相册可增删", "✓ 指定事件可编辑"],
            ["viewer（只看）", "× 永不开放", "✓ 只看被分享的", "✓ 只看被分享的"]]
    cw, ch = 175, 60
    for r, row in enumerate(grid):
        for c, val in enumerate(row):
            x, y = 40 + c * cw, 124 + r * ch
            fill = PANEL if (r == 0 or c == 0) else (WHITE if "✓" in val else "#FDF6F4")
            s.rect(x, y, cw, ch, fill, GRAY, 1)
            weight = "600" if (r == 0 or c == 0) else "normal"
            color = INK if (r == 0 or c == 0) else (TEAL if "✓" in val else RED)
            s.text(x + 12, y + 26, val, 10.4, weight, color)
    s.text(40, 388, "读法：行是「这个人是谁」，列是「授权到哪一层」。空着的组合 = 没有权限，不是待配置。", 10.4, "normal", MUTED)

    # ---------- ② 三张表
    panel(s, 760, 112, 820, 260, "② 三张表就够（不加第四张）")
    s.lines(776, 156, ["family_group   (id, owner_id, name, created_at)",
                       "family_member  (group_id, user_id, role, scope, granted_at)",
                       "handoff_log     (from_user, to_user, object_type, object_id, at, note)",
                       "scope 是「对象级」不是「库级」：album / event 各一条授权记录；撤销 = 删一条 member 行。",
                       "禁区：没有「家庭成员自动可见全部」的开关——根本没有这个字段。",
                       "禁区：没有 owner 之外的「管理员」，避免一个账号被家人拿去全权操作。"], 10.5, 28)
    s.text(776, 344, "为什么不做「权限组」：三张表能表达的，加一层 RBAC 只会多出解释成本。", 10.2, "600", TEAL)

    # ---------- ③ 老人端
    panel(s, 40, 418, 760, 280, "③ 老人端 = 同一 App 的受限形态（需求 42）", WARM, WARM_SOFT, WARM)
    s.lines(56, 462, ["字号：var() token 重写（1.3× / 1.5×），不是另做一套 UI",
                      "交互：大按钮、单列、语音优先、关键动作二次确认",
                      "隐藏：多选批量、复杂筛选、专业术语、任何涉钱入口"],
           10.4, 26)
    s.text(56, 556, "同一份前端代码，两种形态——避免「老人版」变成第二个没人维护的仓库。", 10.2, "normal", MUTED)
    s.text(56, 586, "验收口径：主流程（导入 / 找一张照片 / 听一段语音）在 1.5× 字号下不出横向滚动条。", 10.2, "600", INK2)
    s.text(56, 616, "禁：把「适老」做成一个独立 App（两套埋点、两套权限、两套 bug）。", 10.2, "normal", RED)
    s.text(56, 646, "禁：在老人端出现任何支付/订阅入口——不是隐藏，是代码里没有这个组件。", 10.2, "normal", RED)

    # ---------- ④ 问答引导
    panel(s, 820, 418, 760, 280, "④ 问答引导（需求 43）：把「空时间轴」变成「有素材」", TEAL, TEAL_SOFT, TEAL)
    s.lines(836, 462, ["interview_session 按人生阶段（儿时 / 求学 / 工作 / 家庭）出题",
                       "→ 用户口述（或打字） → ASR → 写入 contents（不是只写画像）",
                       "→ 进事件聚合（L2/L3） → 时间轴立刻有东西可翻",
                       "→ 老人端首页出现「您的故事」条目，而不是空白页"],
           10.4, 26)
    s.text(836, 580, "为什么必须写 contents：只写画像 = 时间轴还是空的，用户看不到自己说的话。", 10.4, "600", RED)
    s.text(836, 610, "为什么必须进聚合：单独一条语音没有「事件」，翻不出来也就分享不出去。", 10.4, "normal", MUTED)
    s.text(836, 646, "配套：每段口述给一条「已收进回忆」回执（需求 30 同口径），不给假进度条。", 10.4, "normal", INK2)

    # ---------- ⑤ 红线
    panel(s, 40, 716, 1540, 196, "⑤ 四条红线（每条都能被门禁断言）", RED, "#FDF6F4", RED)
    s.lines(56, 764, ["1. 可见性最小化：viewer 默认只能看被显式共享的相册/事件，不能全库漫游（G8：无 scope 返回空，不返回全部）。",
                      "2. 交接留痕：妈妈把「宝宝成长册」交给爸爸 → handoff_log 一条；谁在什么时候拿到什么，可追溯。",
                      "3. 术语单一来源（需求 45）：禁「整理 / 清理 / 删除」，用「精选 / 回忆 / 故事」；G10 第 5 轴断言。",
                      "4. 删除权归属：只有 owner 能删组；被共享者只能撤自己的授权，不能删别人的素材（四层 purge 只对本人资产生效）。"],
           10.6, 30)

    s.text(40, 952, "一句话：家庭域的难点不是「怎么共享」，是「怎么保证不该看见的永远看不见」——所以默认值是 ×，不是 ✓。", 11, "600", INK2)
    return s.save("18-家庭权限")




if __name__ == "__main__":
    for fn in (d1, d2, d3, d4, d5, d6, d7, d8, d9, d10, d11, d12, d13, d14, d15, d16, d17, d18):
        print(fn())
