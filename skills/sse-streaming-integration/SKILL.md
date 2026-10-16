---
name: sse-streaming-integration
description: >-
  忆述光华对话域 SSE 流式对接范式（App ↔ backend ↔ agent 三层）：
  backend StreamingResponse 生成器与 `contextvars` 陷阱（同步生成器每次 next() 派线程池 ⇒
  重新绑定 Context）、**消费+落库必须与客户端连接解耦（后台线程 + 有界队列）**、
  流内帧协议（status/preview/tool/replace/done/error 与保活帧）、
  客户端 uni.request `enableChunked` + `onChunkReceived` 手写 UTF-8 解码、
  护栏与流式预览相容（replace 帧同样要过规则层）、结构化 cards/chips 派生。
  **改对话链路（chat_api / TabAi / backend chat.py / agent main.py）前先读。**
official: false
---

# SSE 流式对接（忆述光华对话域）

## 何时读

- 改 `backend/app/api/chat.py` 的流式端点、`agent/src/main.py` 的 `/v1/chat/stream`
- 改 `client/utils/chat_api.uts` 或 `client/components/TabAi/TabAi.uvue`
- 新增任何"边生成边展示"的能力（未来的其他流式场景同样适用）

## 一、帧协议（三层对齐，唯一事实源）

| 帧 | 发送方 | 语义 | 客户端处理 |
|---|---|---|---|
| `status` | backend | 已受理（`{"stage":"accepted"}`） | 忽略（但**喂看门狗**） |
| `preview` | backend | **累计全文**（不是增量！） | 整体覆盖 typing 气泡文本 |
| `replace` | backend | 校验重试后的整段替换（预览作废） | 同 preview（覆盖，非追加） |
| `tool` | backend | 工具阶段（"正在翻你的记忆…"） | 显示工具文案 |
| `done` | backend | 终稿（**落库之后**才发） | 用 reply 替换 typing 气泡 |
| `error` | backend | 流内错误（HTTP 头已发，改不了状态码） | toast + 撤 typing，**绝不伪造回复** |
| `: keepalive` | backend | 注释帧（5s） | 不看内容，只用于**续命 idle 看门狗** |

**要点**：`preview` 是**累计全文**——丢帧只"晚一拍"，不会拼出错字；客户端因此可以"无状态覆盖"渲染，
比增量拼接健壮得多（后者一旦丢帧就永久错位）。

## 二、backend 三条铁律

1. **同步生成器 + `contextvars` 陷阱**：Starlette 会把**同步**生成器的每次 `next()` 派到线程池，
   每次是新 context 快照 ⇒ 请求上下文（user_id 等）在生成器里**取不到**。
   解法（本项目采用）：**整轮 agent 跑在一个工作线程 + 有界队列**，生成器只做"从队列取帧转发"。
2. **消费与落库必须与客户端连接解耦**（2026-10-11 A-1 修复）：原实现把"消费 agent 流 + 护栏 + 落库"
   写在响应生成器里 ⇒ 客户端一断连（90s 看门狗 / 网络闪断 / 切后台）生成器被 close，
   **整轮 agent 被中途放弃、回复永不落库**。现改为后台线程消费到 `done` 并落库，
   断连只停止"投递"（`threading.Event` 门控 `queue.put` 超时丢弃），**回复不丢**。
   ⚠️ 队列必须**有界**（本例 256），否则 agent 卡死时无界堆积。
3. **护栏与流式相容**：`preview` 与 **`replace` 都要过规则层**（`rule_screen`），命中即 `error` 帧中断。
   终稿仍走 `deep=True` 且**不过审不落库不返回**。⚠️ 曾经只检 delta 累积、漏检 replace ⇒
   重试轮命中敏感词照样推屏（A-2，已修）。

## 三、客户端要点

- `uni.request` 需要 `enableChunked: true` + `responseType: 'arraybuffer'`，用 `onChunkReceived` 收块；
- **手写 UTF-8 解码**：chunk 边界会切在多字节字符中间 ⇒ 保留"半个汉字"到下一块
  （回退 ≤3 字节，兜底丢 1 字节），否则偶发乱码；
- **兜底**：部分平台 chunk 回调可能被吞（`api.uts`/`auth.uts` 有同族先例）⇒ `success` 里若
  `chunkSeen == false`，把整段响应体再解析一遍（拿不到逐字效果，但不会丢终稿）；
- **看门狗用 idle 而非总时长**：agent 单轮实测 56~76s、校验重试可破 90s ⇒ 总时长必误杀。
  规则＝**每收到一帧就续命**（backend 每 5s 一条保活注释帧，所以"真卡死"才有 90s 静默）；
- **`finish` 里必须 `clearTimeout`（取消看门狗），不是 `resetWatchdog()`（重置=重新武装）**——
  后者会导致"回复成功后 90s 凭空弹一次超时"（2026-10-11 实测回归）。
- **单帧解析异常必须就地吞掉**（`try/catch` 包住 `handleFrame`）：一行坏数据不能让同 chunk 后续帧陪葬。
- **各请求头统一走 `api.uts` 的 `buildHeader()`**（含 `ngrok-skip-browser-warning`）：
  ⚠️ 历史上 GET 分支自己拼 header ⇒ 加通用头时**漏掉全部 GET**（2026-10-11 已统一）。

## 四、结构化回复（cards / chips）

- `cards` 由 agent 的 `build_reply_meta` 从 `save_memory` 工具结果派生（duplicate/失败**不产卡**）；
  `chips` 由卡片 topics 派生；
- backend `kind` 判定：有 cards ⇒ `cards`；有 chips ⇒ `plain`；多行 ⇒ `plain`；落 `payload`；
  **历史回填同带 cards/chips**（否则"刚收到"与"重进会话"长得不一样）；
- `img` 空串要跳过图片（不要渲染破图）。

## 五、联调与验证

```bash
# 起服务（含 agent）：pwsh -File scripts/dev/start_all.ps1
# 全栈状态：        pwsh -File scripts/dev/status.ps1
# 门禁（含测试）：  python scripts/review_agent.py --full
```

**真机打点**（`chat_api.uts` 内置）：`[dbg-chat] first preview at Xms` / `done at Xms (frames=N, chunks=M)`；
`chunks=0` 恒成立 ⇒ 平台吞了 chunk 回调（走了兜底）。

**A 级验收三条**：① 首字 ≤1.5s；② 护栏拦截**无预览泄漏**（命中即中断，已推前缀必须过审）；
③ agent 宕机**不伪造回复**（撤 typing + 如实 toast）。

## 六、已知坑（都在 lessons 里）

1. Starlette 同步生成器 → 每次 `next()` 新 contextvars 快照（10-09）；
2. 5.24 的 dts 没有 `enableChunked`/`onChunkReceived`/`TextDecoder`，但**编译探针实测可编过**
   （与 `scroll-x` 被拒不同 —— 遇 dts 缺失先编译探针，别急着找替代方案）；
3. 客户端断连 + 服务端"跑完就落库"= 行为契约变更：断线后**回复仍会落库**，
   用户重进会话能看到它（这是**期望行为**，不是重复消息 bug）。
