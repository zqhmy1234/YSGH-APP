# ADR-002: 单一 ContextAssembler，占位符渲染 + 受保护段不裁

- 状态：accepted（2026-11-23）
- 背景：计划 §5.1–§5.3；45 §4.4；设计 §7
- 决策：
  1. 只保留 `harness/context.py::ContextAssembler.assemble` 一个入口；
     `build_frame` 仅作兼容适配器。
  2. 九段固定顺序 `#0..#8`，字符预算 4200 + 输入 2000 = 硬上限 6200。
  3. 裁剪顺序固定 `history → narrative → evidence → digest → persona`；
     `global / corrections / 雷区 / input / output_contract` 永不裁。
  4. `Muse.speak()` 只接受有 `render()` 的上下文对象，禁止调用方传自由 system 字符串。
- 后果：
  - 好处：预算可测、注入路径唯一、角色卡无法移动护栏。
  - 代价：旧调用方必须迁移；`build_frame` 暂时保留但只能走同一实现。
- 门禁：
  - `agent/tests/agentv3/test_frame.py`（9 段/裁剪/保护段）
  - `agent/tests/harness/test_context.py`（三态/字节稳定/预算）
  - judge mutant `history-pruned-first` + probe `probe-3-boundary-never-pruned`
