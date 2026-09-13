# 卡E05 · W5 mock 立即修项（并行开发卡）

> 来源：执行计划 v2 W5 波；2026-09-04 峰宝批准并行开发模式。
> **状态**：🟦 施工中 → 完成后主控改 ✅

## 独占文件域（禁改其他任何文件）

- `client/pages/favorites/favorites.uvue`
- `client/utils/config.uts`

## 公共禁区（对齐 00 公共铁律「并行卡铁律」节）

1. 禁一切 git 写操作——代码改完留着，主控统一提交
2. 禁编译推包、禁 deploy 脚本
3. 不改 `_diff_ledger.md` / `progress.md` / `feature_list.json` / `AGENTS.md`
4. 禁止新增 mock/假数据

## 任务

删除两处已失去存在理由的 mock 残留。

**背景**：favorites 空态会灌 12 条假卡（USE_MOCK_FAVORITES 开关，约 :314-317），真实数据链路已通，假卡只会在有真数据前误导验收；config.uts:29 MOCK_EXTERNAL_AI 全项目零引用（死开关）。

**改动点**：
1. favorites.uvue：删除空态假卡注入逻辑（灌假卡分支 + USE_MOCK_FAVORITES 常量 + 对应 mock 数据构造函数），空态回归「还没有收藏」真空态
2. config.uts：删除 MOCK_EXTERNAL_AI 导出（:29）
3. 两个删除动作都 grep 全项目验证零残余引用——**如有引用方：先在报告里说明，不擅自改独占域外的文件**

## 工程铁律

1. 同文件多处修改串行 Edit，每刀 grep 验刀
2. 删除以「连带引用全清」为完成标准（开关常量、构造函数、分支调用一起清）

## 自查清单（完成定义）

- [ ] grep `USE_MOCK_FAVORITES` / `MOCK_EXTERNAL_AI` 全项目清零
- [ ] favorites 真数据路径（loadCards/fetchTimeline）未被误伤；「还没有收藏」空态文案保留
- [ ] `git status` 确认仅触碰独占文件域
- [ ] 输出结构化报告：改动文件:行号清单、每刀验证证据、未解决问题
