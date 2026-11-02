"""平台适配层（去 Coze）：把上游对 Coze 运行时 SDK 的依赖逐一收敛到本包。

分工（见 agent/UPSTREAM.md 四·改造映射表）：
  - **业务依赖**（转写 / 网页抓取 / 对象存储预签名 / 上下文 / 日志 / 错误分类）→ 本包提供薄适配器
  - **平台编排件**（Coze 的 run/stream_run/node_run、AsyncTaskRuntime、OpenAIChatHandler、
    graph_helper、log.parser、node_log、cozeloop 埋点）→ **不重实现**：它们是 Coze 平台自身的
    任务/流式/观测外壳，与本系统无关；`main.py` 将换成本服务自己的洁净 HTTP 接口（AG4b），
    届时这些依赖整体消失。

原则：适配器只做"等价替换"，**不改变业务语义**；无法等价替换处**显式失败并说明**，
绝不静默降级（本项目铁律）。
"""
