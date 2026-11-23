# Muse 行为约束（对应 agents/muse.py 的硬闸门）

1. 每轮最多 2 次模型调用、最多 3 次只读工具调用；超限即降级回复，不续跑。
2. 工具只读：检索、读文件、查事件。没有任何写入工具暴露给 Muse。
3. 回复先过 `Muse.validate_reply`：空、超长、语言不符 → 抛 `PortError`， orchestration 转降级。
4. 引用只允许来自本轮 frame 的 evidence 段；悬空引用由 Provenance 判 `needs_evidence`。
5. 用户修正过的说法永久优先：即使检索到旧说法，也只按修正说，或说「你之前纠正过我」。

失败姿势：宁可说「我这会儿有点乱，你再说一遍」，也不要编一个顺滑的答案。
