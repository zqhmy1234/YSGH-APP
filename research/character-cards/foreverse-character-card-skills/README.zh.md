# character-card-skills · 角色卡创作 skill + 原创卡集 + AI 味检测器

给角色卡作者的一套开源资产：Agent 创作技能（[SKILL.md 开放规范](https://github.com/anthropics/skills)，Claude Code / Cursor / Codex / Gemini CLI 通用，[Foreverse](https://foreverse.app) App 内置同源版）、18 张原创角色卡（SillyTavern 直接导入，v2 JSON + v3 PNG 嵌卡齐备）、按语言各自校准的 AI 味规则检测器。其中 `skills/character-card-author-en/` 是（据我们所知）agent-skills 生态第一个**英文原生**写卡 skill——规则从英文社区实证重新推导（30 张热门卡采样 + rentry 指南体系 + 平台官方文档），不是中文规则的翻译版；两套规则在社区真实分歧处互相矛盾（开场白带宽 / HTML 面板 / 世界书密度 / em-dash 性质），这正是分开两个 skill 的原因。

这个仓库为什么存在，一个实验说清楚：我们让多个 LLM 评委盲评「真人写的卡 vs AI 写的卡」，评委间一致率 86%——gold 正确率却只有 **12%**，一致地把真人热门卡判成 AI、把 AI 卡判成真人。LLM 评委把细节密度、状态栏、工整句子当人味证据；真实读者拿同样这些特征认出 AI。所以我们不再问模型「这段像不像人写的」，改为把真实读者点名的病句沉淀成规则纪律和确定性检测器。实验全文：[docs/blind-review-experiment.md](docs/blind-review-experiment.md) · [博客版](https://foreverse.app/zh/blog/llm-judges-called-humans-ai) · [发布记](https://foreverse.app/zh/blog/open-sourcing-character-card-skills)。

## 仓库里有什么

| 路径 | 内容 |
| --- | --- |
| [`skills/character-card-author/`](skills/character-card-author/) | 中文创作 skill 正体：四元组选型 → 47 份分类指南（年上禁欲到规则怪谈全谱系）→ 五范式开场白 / 活人感九写法 / 世界书五范式 / 输出节奏四律 / 卡面句式纪律七病灶；附 18 份生图风格深度指南 + 7 张结构范例卡 |
| [`skills/character-card-author-en/`](skills/character-card-author-en/) | **英文原生**创作 skill：方法论骨架与中文版共享，落地层全部按英文社区实证重推——开场白 150–350 词带（30 卡采样中位 178.5）、零 HTML 纪律、labeled prose 人设、no-user-actions 一票否决、AnyPOV 代词工程、permanent token 预算、95 条英文 slop 词表、naming slop 禁用名单 |
| [`skills/chat-quality-doctor/`](skills/chat-quality-doctor/) | 聊后调优门诊：「聊完不满意」的一级分诊台（太AI/小作文/没脾气/记不住/剧情推不动…→ 症状表 → 诊断 → 按作用域手术 → 回测），附 8 份 per-model 症状档案（带置信度标签）+ AI 味套话词表 |
| [`skills/style-anchor-fusion/`](skills/style-anchor-fusion/) | 语料腔调锚融合仿写：写长文前在同题材可融域选 2-3 本真书（骨架/氛围/文笔上限分工）深读成「腔调锚笔记」，再与反 AI 味纪律双管写作——纪律管「禁什么」，语料管词汇/物象/话术层的「像什么」。证据口径经人工质询复核修正：机器验证的是纪律把套话压到 0.00 与语料的词汇层收益，**纯语料不叠纪律=负增益**（复核后被加强）；「盲评第一」降级为 LLM 评委偏好（评委追捧的碎句腔按可测指标恰离真人书最远）。新增碎句腔警戒三禁令+验收纪律；附 15 字滑窗防抄袭闸（`tools/plagiarism_check.py`）与碎句指纹普查（`tools/fragment_census.py`） |
| [`cards/zh/`](cards/zh/)（12 张）· [`cards/en/`](cards/en/)（6 张） | 全原创角色卡，每张 = card.md 源码 + v2 JSON + PNG 嵌卡 + 封面；中文卡过 zh 评分闸，英文新 3 张（Edmund/Frankie/Redline）过 en 评分闸各 94.0；旧 3 张（Rosalind/Caleb/Lighthouse）是「中文方法论走英文轨」的对照组，保持原样入档 |
| [`tools/`](tools/) | zh 评分器 + en 评分器（`score_card_en.py`，八维+五类红线一票否决）/ 独立 AI 味检测 CLI / md→ST 转换器（零依赖）/ 双盲评审 harness |
| [`docs/`](docs/) | [双盲实验报告](docs/blind-review-experiment.md) · [before/after 手术实录](docs/before-after.md) · [矩阵总表](docs/matrix.md) · [card.md 格式规范](docs/card-md-format.md) |

## 用卡

`cards/` 下任意 PNG 拿走即用：SillyTavern「导入角色」、Foreverse 角色页导入、或任何读 `chara`/`ccv3` PNG 块的前端。旁边的 `.v2.json` 是同一张卡的 JSON 形态。世界书、备选开场白、示例对话都内嵌。

## 在 Android 手机上跑酒馆卡

这仓库里的卡都能在手机上玩，问题只是服务器放哪：Termux 本地跑（免费，但 Android 的后台进程查杀专杀 Node 服务）、云服务器自己运维、或者根本不要服务器的原生 App。三条路的真实取舍（具体命令、翻车点、各适合谁）写在 [SillyTavern on Android: three roads](https://foreverse.app/blog/sillytavern-on-android-three-ways)。原生这条路就是 [Foreverse（Google Play）](https://play.google.com/store/apps/details?id=app.foreverse.android)：PNG/JSON/charx 卡、世界书、预设、正则、群聊直接导入，免 Termux 免服务器，自带 key 支持 62 家。

## 用 skill

```bash
git clone https://github.com/foreverse-app/character-card-skills
# Claude Code
cp -r character-card-skills/skills/* ~/.claude/skills/
# Cursor（项目级）
cp -r character-card-skills/skills/* 你的项目/.cursor/skills/
# Codex
cp -r character-card-skills/skills/* ~/.codex/skills/
```

然后对 agent 说「写一张角色卡」——它会走四元组选型、读对应分类指南、按七病灶自查。在 [Foreverse Android App](https://foreverse.app) 里同源 skill 是内置的，原生打通评分/封面生成/一键入库/社区发布，对书架 agent 说一句话就行；还能读你书架上的小说直接提炼角色卡。

## 用工具

```bash
python3 tools/score_card.py cards/zh/*/card.md         # 八维评分闸，<75 退出码 1
python3 tools/ai_flavor_check.py 某段文案.txt           # 独立 AI 味体检
python3 tools/plagiarism_check.py --corpus 语料书.txt \
    --target 成稿目录/                                  # 仿写防抄袭滑窗闸（15 字窗）
python3 tools/fragment_census.py --target 成稿目录/     # 碎句指纹普查 vs 真人书区间（中文稿）
python3 tools/card_md_to_st.py cards/zh/peiyu/card.md --slug peiyu \
    --avatar cards/zh/peiyu/cover.png --outdir out/     # md → v2 JSON + v3 PNG
python3 tools/tests/test_ai_flavor_gold.py              # 检测器 gold 回归
```

CI 每次 push 对全部中文卡跑评分闸（≥75 + 反 AI 味 5/5 锁）、跑检测器 gold 回归、对全部卡做转换 round-trip。

## 纪律长什么样：一处 before/after

我们一张结构分 91.2 的卡，被真实读者一眼判 AI，还点名了句子。那次教训变成了「卡面句式纪律」（七病灶配额制）：

> **Before**：大二摔栏摔的，她说那是「学费」。——每个设定都让角色说句俏皮话盖章；均匀的机灵 = AI。
> **After**：大二跨栏的时候摔过一次，锁骨下面留了道浅疤。——信息平铺；全卡俏皮话预算 1 处。

完整七病灶手术实录（含检测器证据）：[docs/before-after.md](docs/before-after.md)。反直觉的地方在于：除 AI 味主要是**做减法**——删金句、删空镜、留闲笔；连检测器自己也要做减法（「一丝凉意」这类描景词人类也写，孤立词不判死刑，句式模式才计分）。

## 覆盖面

47 份分类指南（女性向 15 / 男性向 9 / 通用 18 / 特殊品类 5），每份含受众爽点解构、原型库、30 轮节奏表、世界书定制表、生图配方与 10+ 项分类专属评分表；18 份生图风格指南；8 份模型症状档案（置信度四级，`stale` 档禁止引用精确数字——这条纪律 doctor skill 对自己也执行）。全表见 [docs/matrix.md](docs/matrix.md)。

## 卡集一览

![card covers](docs/assets/gallery.jpg)

宋知夏（校园·受伤嘴硬的短跑女王）/ 江迟野（校园·画速写的不良少年）/ 霍聿深（职场·锁着抽屉的上司）/ 奈芙尔（奇幻·被诅咒的猫耳炼金术师）/ 苏念栀（校园·系统流女主）/ 裴聿（古风·大理寺连环案）/ 沈砚（都市·画廊复仇金主）/ 纪闻笙（都市·凌晨琴房的作曲家未婚夫）/ 唐团团（日常·打烊前的面包店老板娘）/ 玄净（仙侠·三千岁剑尊师徒）/ 聂小倩（聊斋同人，公版底本）/ 晚孤市（模拟器·梦境夜市多 NPC）+ 英文原生卡 3 张（制图师宿敌 Edmund Harrower / 深夜餐馆 Frankie Doyle / 打捞船调度台 Redline Salvage Co.，en 评分闸各 94.0）+ 英文对照组 3 张（击剑教练 Rosalind / 双面人生 Caleb / 1897 灯塔规则怪谈 The Lighthouse——中文方法论走英文轨的历史产物，保留供对照）。

分数见 [README.md](README.md) 卡表（中文卡走 zh 评分器、英文原生卡走 en 评分器，两套口径各自校准、互不可比）；全部卡过反 AI 味全卡扫描。内容说明：分类指南覆盖成人向品类的**叙事方法论**（各带合规条款），仓库成品卡本身全部 SFW——张力在纸面上，不写露骨内容。

## 关于 Foreverse

我们在做 [Foreverse](https://foreverse.app)：一个免费 Android App（App 本体闭源，本仓库是独立可用的开源部分），把小说阅读、剧情分支和角色聊天放进同一个书架——导入自己的 TXT/EPUB，从任意段落分叉剧情，和书里的角色无剧透聊天，或者把整本书交给书架上的 agent 让它提炼角色卡。深度 SillyTavern 兼容（PNG/JSON/charx、世界书、预设、正则、群聊），BYOK 60+ 供应商。这套 skill 就是 App 内置 agent skill 的开源版；统计基线来自对创作社区公开作品的阅读与统计，开源是还这份人情。[Google Play](https://play.google.com/store/apps/details?id=app.foreverse.android) · [foreverse.app](https://foreverse.app) · [@foreverseapp](https://x.com/foreverseapp)

## 贡献与许可

欢迎 issue/PR——投稿卡须过 `tools/score_card.py`（≥75 且反 AI 味 5/5）和所属分类的专属评分表，前者 CI 自动把关。代码 [MIT](LICENSE)；skill/卡/文档 [CC BY 4.0](LICENSE-CONTENT)（署名指向本仓库或 foreverse.app）。全部卡为原创；封面均为我方 AI 生成并在各卡作者的话里如实署名。
