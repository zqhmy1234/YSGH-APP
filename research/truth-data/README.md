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

## 隐私铁律（不可绕过）

- `user_id_hashed`：sha256 脱敏，禁止明文手机号/微信 id
- C 批 `self_talk_only` 必须 true（录音只收自述片段）
- D 批照片导出前必须人脸打码（腾讯 CI 检测 + 高斯模糊）
- 涉第三方信息字段置空

## 规格

见 [docs/真值数据规格标准_v1.md](../../docs/真值数据规格标准_v1.md)（第一版完整版，含 A-H 八批全部规格）。字段以该文档 + `templates/` 模板 + 校验器 `scripts/validate_truth_data.py` 三者为一致口径。
