# Character Card 研究样本（只读）

本目录保存公开可获取 / 自有旧文案的 Character Card 研究样本，用于：

1. 归纳 V2 字段与 lorebook 常见结构；
2. 为 `agent/assets/cards/*.json` 内部 RoleCard 提供改写素材；
3. 记录许可与来源，便于审计。

**禁止**：把本目录任何文件直接复制进 `agent/assets/cards/` 作为产品运行时卡。
产品卡的唯一运行时对象是内部 `RoleCard`（`agent/src/prompts/role_card.py`）；
V2 只在导入/导出时由 `prompts/card_v2.py` 适配。

## 已知结论（2026-11-23）

- 下载样本普遍带 `system_prompt` / `post_history_instructions` / `character_book`；
  直接当运行时会把这些作者设定带进产品，这是必须做内部 RoleCard 的硬理由。
- 本仓库目前没有把第三方卡入库；`agent/assets/cards/` 的 12 张卡均为自写。

## 许可记录模板

| 文件 | 来源 URL | 作者 | 许可 | 下载日期 | 用途 |
|---|---|---|---|---|---|
| （待补） | | | | | 仅研究 |
