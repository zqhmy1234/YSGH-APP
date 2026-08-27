# 忆述光华 · 文案库（docs/copy_library）——Agent C2 交付

> 域：**文案与模板数据域**（Wave 1 · 契约 C8 + 拍板⑤）｜交付：Agent C2（2026-08-28）
> 目标：让 **US-21 关怀链路与模板池不再被文案阻塞**——AI 起草默认文案，产品部正式文案到位后**仅替换 JSON 内容，触发逻辑不动**。

## 1. 本目录文件

| 文件 | 作用 | 消费方 |
|---|---|---|
| `schema.json` | 文案库契约（JSON Schema draft-07）：数据文件形状 + `x-contract` 契约常量 + 占位符词表 | B2 加载器 + 校验脚本共同契约 |
| `care_copy.json` | **关怀默认文案库**：对齐 notify.py `CARE_TEMPLATES` 6 场景 × 每场景 ≥3 候选 | B2 加载器读入 → 回填 CARE_TEMPLATES |
| `template_pool.json` | **追问/回响模板骨架池**（40 条，区间 30–50）：echo 回响 20 + followup 追问 20，按情绪/场景/时段分层 | 生成侧骨架参考（追问链/回响引导） |
| `README.md` | 本说明 | 人 + 集成 Agent |

## 2. schema.json 要点（B2 加载器核对用）

- **场景键**：`sad_ask / sad_respond / angry / late_night / day2 / day3`——与 `backend/app/services/notify.py` `CARE_TEMPLATES` 6 键**完全一致**（校验脚本强制 `scenarios` 键集合 == 契约键，多一个少一个都报错）。
- **触发语义**（`x-contract.scenario_trigger_semantics`，与 notify.py `maybe_send_emotion_care` 对齐）：
  - `sad_ask`：SAD 未说明原因 → 关怀追问
  - `sad_respond`：SAD 已说明原因 → 回应（『辛苦了』）
  - `angry`：ANGRY → 陪伴出口（兼作恐惧/厌恶/惊讶等其他负面的默认陪伴出口）
  - `late_night`：深夜 22:00–05:00 → 轻量表达、不催回复
  - `day2`：近 3 天 streak≥1（第 2 天）→ 『好些了吗』
  - `day3`：近 3 天 streak≥2（第 3 天起）→ 只陪伴不追问
  - **<0.7 不触发**：`x-contract.emotion_action_threshold=0.7`（confidence<0.7 只存档案不打扰，由加载器统一门控，不写在场景适用条件里）。
- **占位符规范**（`x-contract.placeholders.vocabulary`）：`{name}/{time}/{period}/{place}/{snippet}/{content_type}/{day}/{n}` 八枚，仅模板骨架池可用；**care_copy.json 的 title/body 为直发原文，禁用占位符**（避免裸 `{xxx}` 泄漏给用户）。
- **候选数组**：每场景 `candidates ≥3` 条（`rotation`：round_robin/random/fixed），加载器轮换去机械感。

## 3. 与 B2 加载器对接（`backend/app/services/copy_library.py`，B2 实现）

约定（见 `x-contract.loader_contract`）：

```text
加载器读 docs/copy_library/care_copy.json
  ├─ 文件存在且可解析 → 按场景 rotation 轮换取 candidates 中一条 {title, body}
  │      → 回填 CARE_TEMPLATES[scenario_key] = {title, body}
  └─ 文件缺失 / 解析失败 / 某键缺失 → 回退 notify.py CARE_TEMPLATES 内置占位
       （绝不抛错打断触发逻辑）
触发语义（maybe_send_emotion_care 分支）零改动，只换数据源。
```

- 加载失败必须**静默回退**（fail-safe：护栏/关怀不因文案缺失而断链）。
- `template_pool.json` 为骨架层（带占位符），供**生成侧**拼装追问链/回响引导用；与 `care_copy.json`（直发原文）用途不同，加载器可只消费 care_copy。

## 4. 产品部替换流程（只改 JSON 不碰代码）

> 拍板⑤：正式文案由产品部提供后替换；**触发逻辑、schema、加载器一律不动**。

1. **改关怀文案**：编辑 `docs/copy_library/care_copy.json` 的 `scenarios.<键>.candidates`（每场景保留 ≥3 条，`title`/`body` 非空、**不含占位符**、不主动提及 `tone_policy.禁止主动提及` 清单内话题）。
2. **改模板骨架**（可选）：编辑 `docs/copy_library/template_pool.json` 的 `skeleton`/`variants`，占位符须在词表内且 `placeholders` 声明与使用闭合。
3. **跑校验**：
   ```bash
   python scripts/validate_copy_library.py
   ```
   `0` = 全绿可交付；`1` = 有阻断项（键对齐/数量区间/占位符闭合/JSON 可解析任一不过则阻断）。
4. **提交**：产品部改动走 `feat(wrap1-agentC2):` 前缀（或集成 Agent 统一收口），**只含数据文件，不含 backend/**。
5. 部署侧：后端加载器读到新 JSON 即生效（无重启耦合点；若加载有缓存由 B2 注明刷新策略）。

## 5. 文案基调（拍板⑤ · 待用户/产品部审阅）

> ⚠️ **本库文案为 AI 起草（Agent C2）**，基调按拍板⑤默认：**温和克制、不提敏感话题、频次递减**。正式上线前**需产品部/用户审阅**（当前每条 `notes` 标注"AI 起草"，替换后改为产品部来源即可）。

- **温和克制**：短句、柔语气、不煽情不说教；尊重用户空间，不催不逼。
- **不提敏感话题**：`tone_policy.禁止主动提及 = [前任, 创伤, 离世, 失恋, 分手, 疾病, 离婚, 亲人去世, 职场PUA, 霸凌]`——正文/标题一律不主动提及，只给陪伴出口（校验脚本对命中词给警告）。
- **频次递减**：连续多日负面时第 1 天温和追问 → 第 2 天『好些了吗』 → 第 3 天起只陪伴不追问。

## 6. 校验与回归

```bash
# 全量校验（三文件 + 契约对齐 + 占位符闭合 + 数量区间）
python scripts/validate_copy_library.py

# 输出报告留档（可选）
python scripts/validate_copy_library.py --report .cowork-temp/copy_library_report.md
```

- 校验脚本独立无第三方依赖（jsonschema 存在时额外做 draft-07 语法校验）。
- 契约真值（6 键/阈值 0.7/深夜 22-05/回看 3 天）以 notify.py 为准，改动 notify 常量须同步本库 `x-contract` 与校验脚本，三者一致才放行。
