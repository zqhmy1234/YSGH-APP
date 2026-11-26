# D1 照片事件聚合评测

使用项目后端 Python 环境，在仓库根目录运行。先导入当前工作簿的 `D1批_连续照片事件分组` 工作表。

```powershell
python -m research.aggregation_benchmark.run_eval `
  --workbook "$env:USERPROFILE/Downloads/D批_照片事件标注表.xlsx" `
  --output .cowork-temp/d1-import.json
```

当前表从第 12 行起是实际标注。表格更新后按实际起始行设置 `--first-data-row`。导入会校验表头和照片 ID，重复 ID 或缺少最终事件编号会报出原表行号。事件名称存在不同写法时，报告提示需复核的事件编号，成员仍按最终事件编号分组。

有算法结果时，在同一命令加 `--predictions <聚合输出.json>`，并指定计分报告的输出路径。每个预测组使用独立的组 ID，照片 ID 使用工作簿中的写法。同一次评测中每张照片最多归入一个组，跨集合的预测组 ID 也须唯一。`groups: []` 表示算法运行后没有产生任何分组。实际输出用 `kind: "algorithm_output"`，示例和测试用 `kind: "synthetic"`。

```json
{
  "run_id": "d1-example",
  "kind": "synthetic",
  "groups": [
    {"id": "event-1", "photo_ids": ["example-1.jpg", "example-2.jpg"]},
    {"id": "event-2", "photo_ids": ["example-3.jpg"]}
  ]
}
```

评测按共享成员数做一对一最优匹配，输出逐组和全局成员 Precision、Recall、F1。被拆出的正确成员会计入漏分，混进匹配组的成员以及无匹配预测组中的成员会计入多归。报告还单列被拆开的人工组、合并了不同人工组的预测组、未分组照片和表外照片 ID。指标分母为零时输出 `null`。

本命令使用 D1 表的最终事件编号作为参考，报告保留标注共识和素材待核验的状态。正式分层验收还需关联脱敏素材、核实标注共识并确认事件编号与聚合层的对应。报告只记录组、照片 ID、行号和输入指纹，建议与工作簿一起留在本地。
