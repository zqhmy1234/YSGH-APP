# 忆述光华 · 文案库（docs/copy_library）——Agent C2 交付 v2

> 域：**文案与模板数据域**（Wave 1 · 契约 C8 + 拍板⑤）｜交付：Agent C2（2026-08-28，v2 字段级细化）
> 目标：让 **US-21 关怀链路与模板池不再被文案阻塞**——AI 起草默认文案，产品部正式文案到位后**仅替换 JSON 内容，触发逻辑不动**。

## 1. 本目录文件

| 文件 | 作用 | 消费方 |
|---|---|---|
| `schema.json` | 文案库契约（JSON Schema draft-07）：顶层 `scenes` 6 键 + `definitions` 数据形状 + `x-contract` 契约常量 + 占位符词表 | B2 加载器 + 校验脚本共同契约 |
| `care_copy.json` | **关怀默认文案库**：`scenes[]` 6 场景 × 每场景 `variants` ≥3 条候选（id/title/body/tone/conditions/placeholders） | B2 加载器读入 → 回填 CARE_TEMPLATES |
| `template_pool.json` | **追问/回响/陪伴模板骨架池**（40 条，区间 30–50）：echo 16 + ask 14 + companion 10，三层按内容类型/时段/情绪/频次分层 | 生成侧骨架参考 |
| `README.md` | 本说明 | 人 + 集成 Agent |

## 2. schema.json 要点（B2 加载器核对用）

- **顶层**：`schema_version: "1.0"`、`updated_at`、`scenes` = 6 场景键清单。
- **场景键**：`sad_ask / sad_respond / angry / late_night / day2 / day3`——与 `backend/app/services/notify.py` `CARE_TEMPLATES` 6 键**完全一致**（校验脚本强制 care_copy `scenes[].scene` 集合 == 6 键，多一个少一个都报错）。
- **触发语义**（`x-contract.scenario_trigger_semantics`，与 notify.py `maybe_send_emotion_care` 对齐）：
  - `sad_ask`：SAD 未说明原因 → 关怀追问｜`sad_respond`：已说明原因 → 回应（『辛苦了』）
  - `angry`：ANGRY → 陪伴出口（兼作恐惧/厌恶/惊讶等默认陪伴出口）
  - `late_night`：深夜 22:00–05:00 → 轻量、不催回复
  - `day2`：近 3 天 streak≥1 → 『好些了吗』｜`day3`：streak≥2 → 只陪伴
  - **<0.7 不触发**：`x-contract.emotion_action_threshold=0.7`（由后端门控，数据层不重复）。
- **care_copy variants 字段**：`id`（唯一，`<scene>_<n>`）、`title` ≤20 字、`body` ≤100 字、`tone` ∈ `gentle/warm/light`（深夜用 `light`）、`conditions`（可选：`emotion/time_range/streak_days`）、`placeholders`（可选；MVP 默认空数组，body 直发原文）。
- **template_pool 三层**：`scene` ∈ `echo`（回响）/`ask`（追问）/`companion`（陪伴）；`template` 骨架含占位符、`variants` ≥2、`conditions` 可选。三层分布建议 `echo 12–18 / ask 10–16 / companion 8–16`（总量 30–50）。
- **占位符规范**（`x-contract.placeholders.vocabulary`）：`{name}/{time}/{period}/{place}/{snippet}/{content_type}/{day}/{n}` 八枚；所有 `{` 必须成对闭合；care_copy 直发原文默认零占位符。

## 3. 与 B2 加载器对接（`backend/app/services/copy_library.py`，B2 实现）

约定（见 `x-contract.loader_contract`）：

```text
加载器读 docs/copy_library/care_copy.json
  ├─ 文件存在且可解析 → 按场景取 variants 中一条（轮换）→ 填回 CARE_TEMPLATES[scene] = {title, body}
  └─ 文件缺失 / 解析失败 / 某场景键缺失 → 回退 notify.py CARE_TEMPLATES 内置占位
       （绝不抛错打断触发逻辑）
触发语义（maybe_send_emotion_care 分支）零改动，只换数据源。
```

- 加载失败必须**静默回退**（fail-safe：关怀不因文案缺失断链）。
- `template_pool.json` 为骨架层（带占位符），供**生成侧**拼装追问链/回响引导用；与 `care_copy.json`（直发原文）用途不同，加载器可只消费 care_copy。

## 4. 产品部替换流程（只改 JSON 不碰代码）

> 拍板⑤：正式文案由产品部提供后替换；**触发逻辑、schema、加载器一律不动**。

1. **改关怀文案**：编辑 `docs/copy_library/care_copy.json` 的 `scenes[].variants`——每场景保留 ≥3 条；`title` ≤20 字、`body` ≤100 字、`tone` 三枚举；**不主动提及** `tone_policy.禁止主动提及` 清单内话题；正文如用占位符必须先在 `placeholders` 声明。
2. **改模板骨架**（可选）：编辑 `docs/copy_library/template_pool.json` 的 `template`/`variants`，`{占位符}` 须成对闭合且属于词表。
3. **跑校验**：
   ```bash
   python scripts/validate_copy_library.py
   ```
   `0` = 全绿可交付；`1` = 有阻断项（JSON 可解析/键对齐/数量区间/占位符闭合/长度枚举/唯一性任一不过则阻断；敏感词命中仅警告）。
4. **提交**：产品部改动走 `feat(wrap1-agentC2):` 前缀（或集成 Agent 统一收口），**只含数据文件，不含 backend/**。
5. 部署侧：后端加载器读到新 JSON 即生效（若加载有缓存由 B2 注明刷新策略）。

## 5. 文案基调（拍板⑤ · 待用户/产品部审阅）

> ⚠️ **本库文案为 AI 起草（Agent C2）**，基调按拍板⑤默认：**温和克制、不提敏感话题、每场景≥3 候选、频次递减**。正式上线前**需产品部/用户审阅**（每条 `notes` 标注"AI 起草"，替换后改为产品部来源即可）。

- **温和克制**：短句、柔语气、不煽情不说教；尊重用户空间，不催不逼。
- **不提敏感话题**：`tone_policy.禁止主动提及 = [前任, 创伤, 离世, 失恋, 分手, 疾病, 离婚, 亲人去世, 职场PUA, 霸凌]`——正文/标题一律不主动提及，只给陪伴出口（校验脚本对命中词给警告）。
- **频次递减**：连续多日负面时第 1 天温和追问 → 第 2 天『好些了吗』 → 第 3 天起只陪伴不追问。

## 6. 校验与回归

```bash
# 全量校验（三文件 + 契约对齐 + 占位符闭合 + 数量区间 + 长度/枚举 + 分层分布）
python scripts/validate_copy_library.py

# 输出报告留档（可选）
python scripts/validate_copy_library.py --report .cowork-temp/copy_library_report.md
```

- 校验脚本独立无第三方依赖（jsonschema 存在时额外做 draft-07 语法校验）。
- 契约真值（6 键/阈值 0.7/深夜 22-05/回看 3 天）以 notify.py 为准，改动 notify 常量须同步本库 `x-contract` 与校验脚本，三者一致才放行。
