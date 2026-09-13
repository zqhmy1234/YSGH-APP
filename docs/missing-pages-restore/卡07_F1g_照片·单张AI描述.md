# 卡07 · F1g 照片·单张AI描述

> **先读** `docs/missing-pages-restore/00_公共铁律.md`，本文档只写本卡差异。

## 真值
- `.wt/missing-pages/uvue_gen/record_f1g_canvas.json`（390x844 · 33 节点 · 2026-09-02 导出，帧号 F1g）
- 关键文案抽校：9:41 / 照片 / 照片 · 1/9 / AI 描述：傍晚的江边，梧桐叶沙沙作响 / 分类 / AI 已预选 · 点标签可纠正 / 日常生活 / 家人与朋友

## 任务
RecordSheet 重写·照片 1/9：单张照片 + AI 描述「傍晚的江边，梧桐叶沙沙作响」、分类 chips

## 目标文件白名单（白名单外零字节）
- `components/RecordSheet/RecordSheet.uvue`
- `components/RecordSheet/ 下新增子组件`

## 冲突
⚠️ 冲突组 RECORD：F1a–F1k 共 11 卡同改 RecordSheet.uvue —— 必须同一 Agent 串行完成（卡拆开只为逐帧验收），禁止分给多个 Agent 并行


## 交互接线
AI 描述文案按帧真值写死进演示数据（USE_MOCK 模式）

## 收尾（本卡）
1. 自审：对照 JSON 节点清单逐个核对（文案/颜色/间距/层级），孤儿 CSS 0，自评 ≥75 分。
2. 降级逐条追加 `_diff_ledger.md`（标注「卡07 F1g」）。
3. `git status --short` 白名单自查贴进汇报；不 commit。
