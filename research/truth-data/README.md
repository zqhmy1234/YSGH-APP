# research/truth-data · 真值数据目录

产品部采集的真实用户真值数据统一放这里，评测 harness 消费。

## 目录结构

```
truth-data/
├── README.md                  # 本文件
├── templates/                 # 交付模板（示例条目）
│   ├── a_search_query.example.json
│   ├── b_fragment.example.json
│   ├── c_voice_clip.example.json
│   ├── d_photo_event.example.json
│   ├── e_correction.example.json
│   ├── f_profile_label.example.json
│   ├── g_guardrail.example.json
│   └── h_ocr.example.json
├── a/  # 搜索查询评测集（≥50 条）
├── b/  # 文字碎片（100-200 条）
├── c/  # 语音片段（≥60 段，audio/ 子目录存音频）
├── d/  # 照片事件真值（D1 短期 50-100 张/3-5 天 + D2 长期 30 天，photos/texts/voices 子目录存打码副本）
├── e/  # 纠错样本（10-20 条，从 B 批复用）
├── f/  # 画像标注真值（50-80 条，轻量批次）
├── g/  # 护栏基准真值（100-150 条）
└── h/  # OCR 基准真值（60-100 张，images/ 子目录存图）
```

## 流程

1. 产品部按 `templates/` 模板导出 JSON（每周五），命名 `{批次}_v{n}.json`
2. 放入对应子目录
3. 跑校验：`python scripts/validate_truth_data.py`（必填/枚举/隐私/一致性）
4. 全绿后更新该批 `manifest.json`，评测 harness 消费

## 产品部现在交的是 xlsx（不是 JSON）→ 先跑转换脚本

产品部交付的是 **xlsx 标注表**（放在 **`<工作区>/真值数据/标注表/`**，仓库外；
原始素材放同级的 `真值数据/素材/`）。xlsx → JSON 这一步
由 `scripts/convert_truth_xlsx.py` 完成 —— **不要手工往这里贴 JSON**。

> 2026-10-09 起，这批数据从 `<工作区>` 根目录整体挪进了 `真值数据/`，目录说明见
> `<工作区>/真值数据/README.md`。脚本默认路径已同步（改前是工作区根）；
> 如果换了位置，用 `--xlsx-dir` 指过去。

```bash
export TRUTH_HASH_SALT='<本地随机串>'      # 或用 --init-salt 生成 ./.salt（已 gitignore）
python scripts/convert_truth_xlsx.py                    # 全批次
python scripts/convert_truth_xlsx.py --batch b          # 只转单批
python scripts/convert_truth_xlsx.py --strict-layer     # A 批 layer 不做推断
python scripts/validate_truth_data.py                   # 转换后必跑
```

三个要知道的点：

- **salt 必须显式提供**，缺失时脚本 exit 2 拒绝运行。salt 变了哈希就变，
  同一用户在 B/C 批会算出两个 `user_id_hashed`，跨批一致性直接破坏。
  优先环境变量 `TRUTH_HASH_SALT`，其次本目录的 `.salt`（`--init-salt` 生成）。
- **宽容模式**：能映射的字段全落盘，不能映射的条目**逐条跳过**并写入
  `_reports/blockers.md`（人读）与 `_reports/blockers.json`（机读），
  精确到「文件 / sheet / 行号 / 记录 id / 原因」。转换后先看这份清单再谈评测。
- **D 批与 F 批当前只出骨架**（`_reports/skeleton_{batch}.json`），**不写正式产物**。
  D 批缺事件分组共识（`set_id`/`window`/`consensus`/`annotator_count`），
  F 批 sheet 无数据行。写空数组会让校验器打印「0 条 ✅」造成假绿，故刻意不写。

## 隐私铁律（不可绕过）

- `user_id_hashed`：sha256 脱敏，禁止明文手机号/微信 id
- C 批 `self_talk_only` 必须 true（录音只收自述片段）
- D 批照片导出前必须人脸打码（腾讯 CI 检测 + 高斯模糊）
- 涉第三方信息字段置空

## ⚠️ 本目录下的真实数据不入公共库

本目录的 `.gitignore` 忽略 `a/`~`h/` 全部批次目录、`.salt`、`_reports/`，
只保留 `README.md` 与 `templates/`。原因：真实数据含聊天记录、语音逐字转写、
家庭住址级定位，实测 B 批文本里还夹着一段明文 API key。

口径见 `docs/RAG评测体系与门禁标准.md` §7.3 —— corpus 应存**独立私有 git 仓库**。

> `a/a_v1.json` 是**历史示例**（3 条，非真实数据），当前仍被 git 跟踪。
> 转换脚本不会覆盖它（自动改用 `a_v2.json`）。如需彻底摘除跟踪，执行
> `git rm --cached research/truth-data/a/a_v1.json`。

## 规格

见 [docs/真值数据规格标准_v1.md](../../docs/真值数据规格标准_v1.md)（第一版完整版，含 A-H 八批全部规格）。字段以该文档 + `templates/` 模板 + 校验器 `scripts/validate_truth_data.py` 三者为一致口径。
