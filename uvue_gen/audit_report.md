# UI 像素级还原对拍报告

> ⚠️ **本报告已失效（2026-09-24 标注 · D12-7 声明漂移）**
>
> ① 所引「对拍器 `scripts_verify_layout.py`」**全仓不存在**（含 git 全历史）⇒ 结论**不可复现**；
>    （全仓现存仅为 `scripts_ardot2uvue.py` / `scripts_assemble.py` / `gen_design_data_v4.py`）
> ② 其覆盖的老页面（`index` / `search` / `profile` / `ai/*` / `press`）**已整体退役**——搬迁为
>    `client/components/Tab{Index,Ai,Search,Profile}/`（见 `client/pages/shell/shell.uvue:7` 注）⇒ 对拍对象已不存在；
> ③ 因此下文「合计：对拍 561 元素，超差 0（0.0%）」**不再作为任何验收依据**（原文保留仅供历史对照）；
> ④ 现行像素/视觉验收走**真机**（见 `skills/android-media-e2e/SKILL.md`），不复用本报告口径。

对拍器：scripts_verify_layout.py（容差 2px，单位 px，坐标已扣除 .phone 容器偏移）

超差归类：转换器规则bug / 缺资产（img naturalWidth=0，见 uvue_gen/_missing_icons.json）/期望计算边界（flex 流式期望无法表达的布局，不参与对拍则不计超差）

**合计：对拍 561 元素，超差 0（0.0%）**

| 页面 | 对拍数 | 超差数 | 超差率 |
| --- | --- | --- | --- |
| profile | 55 | 0 | 0% |
| index | 46 | 0 | 0% |
| search | 49 | 0 | 0% |
| messages | 35 | 0 | 0% |
| settings | 24 | 0 | 0% |
| interview | 29 | 0 | 0% |
| detail | 41 | 0 | 0% |
| ai | 45 | 0 | 0% |
| record | 71 | 0 | 0% |
| empty | 13 | 0 | 0% |
| ai_input | 38 | 0 | 0% |
| ai_reply | 52 | 0 | 0% |
| ai_typing | 43 | 0 | 0% |
| press | 20 | 0 | 0% |

## profile（超差 0/55）


## index（超差 0/46）


## search（超差 0/49）


## messages（超差 0/35）


## settings（超差 0/24）


## interview（超差 0/29）


## detail（超差 0/41）


## ai（超差 0/45）


## record（超差 0/71）


## empty（超差 0/13）


## ai_input（超差 0/38）


## ai_reply（超差 0/52）


## ai_typing（超差 0/43）


## press（超差 0/20）


## 遗留：缺资产（28 个图标待下载，宽度按 CSS 尺寸渲染不影响布局对拍）

`4:43` `4:85` `4:86` `4:62` `4:64` `4:66` `4:68` `4:53` `4:7` `4:12` `4:17` `4:22` `2:302` `2:304` `4:260` `4:273` `4:279` `4:282` `4:284` `4:308` `4:341` `4:376` `4:380` `4:394` `2:319` `2:320` `2:329` `2:330`
