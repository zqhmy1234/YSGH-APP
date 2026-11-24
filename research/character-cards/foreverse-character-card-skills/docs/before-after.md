# Before / After：一张 91.2 分的卡为什么被真实读者一眼判 AI

*The card scored 91.2/100 on our structural rubric. A real reader called it AI-written on sight — and named the exact sentences. This page is the surgery record.（宋知夏卡 v1 → v1.1 除 AI 味手术实录；七病灶配额制由此诞生，现为 `skills/character-card-author/SKILL.md` §2.6。）*

先说结论：**评分器满意 ≠ 人味。** v1 的每一处都符合「好卡」的规格——细节具体、句句有信息、结构完整。问题恰恰出在这：**规格完美本身就是机器指纹**。人类写手平铺信息、偶尔才俏皮一次；均匀的机灵 = AI。

## 七病灶 · before → after

### 1. 引语点题（quote-capping）

把设定包装成角色俏皮引语盖章——读者点名的第一处。

> **Before**：大二摔栏摔的，她说那是「学费」。
> **After**：大二跨栏的时候摔过一次，锁骨下面留了道浅疤。

（英译注：before = *she got it hurdling in sophomore year — "tuition," she calls it*；after = 平铺事实，俏皮话删掉。全卡这类结构只允许留 1 处当梗位。）

### 2. 对仗翻转（antithesis flip）

「不带X，全是Y」——读者点名的第二处。v1 里「嘴上X，行为Y」模具全卡出现 4 次。

> **Before**：骂人不带脏字，全是事实句。
> **After**：嘴毒，但说的都是实话，所以更气人。

（before 是一个精心磨过的「金句」；after 是一个人随口会说的话。）

### 3. 生造概念词（invented concept words）

机制词只许在 system_prompt 与世界书里活动，出现在可见文案即穿帮。

> **Before**：这瓶牛奶是她的「胜利锚点」；生人勿近是她的保护色。
> **After**：大一破纪录那晚喝的就是这个，从此变成她的小仪式。／防备心很重。

### 4. 金句癖 + 意味深长空镜（aphorism addiction）

> **Before**：医生说三周，教练说等，她说：我的腿我知道。／记分牌的灯还停在上周的成绩页。／七个欲言又止的省略号。
> **After**：医生让休三周，她不听，教练实在按不住她，就把你调过来了。

（三连排比、空镜隐喻全删。段落允许平收甚至烂尾。）

### 5. 伪精确数字（fake-precision numbers）

v1 数字轰炸：10秒98 / 0.05秒 / 18:40 / 第9天 / −40天 / 21公里 / 0.75倍速——「具体性表演」。

> **Before**：10 秒 98 的校纪录保持者，静养第 9 天，距省锦标赛还有 40 天。
> **After**：大一跑进 10 秒 9 几，现在距离省锦标赛就剩一个多月了。

（保留的数字要口语。「半X」量词模具——停顿了半秒/退后半步/快了半拍——同属此病：特征挖掘实测 AI 簇状出现、人类基线为 0。）

### 6. 说明书腔外漏（spec-sheet leakage）

> **Before**（Description 可见文案里）：破功瞬间：疼到极限会有半声吸气，然后立刻说鞋带勒的。
> **After**：这句话原样搬进 system_prompt（它是给模型的行为规格），Description 里删除。

作者的话同病：v1 的 Creator Notes 写着「对标◯◯／差异化／玩法核心」——产品文档腔。v1.1 改成社区写手碎碎念：「知夏只爱user，望周知！……不喜欢这种带点刺的学姐请左上角退出。」

### 7. 零闲笔（zero idle details）

v1 每个细节都是功能件：牛奶=锚点、钉鞋=信号器、代跑奖牌=关心实证、疼痛差值=好感度进度条——信息压强均匀，一读就是生成的。修复不是删细节，是**加白长的**：

> **After 新增**：「还站着干嘛？体育馆八点锁门，要走顺便帮我把灯关了。」

（这句不服务任何设定。人类写手会留自嗨的废话；全卡至少一处。）

## 检测层证据（可复现）

```bash
# v1.1（本仓库 cards/zh/songzhixia/card.md）：反 AI 味 5/5 满分
python3 tools/score_card.py cards/zh/songzhixia/card.md

# 读者点名病句：检测器必须命中（回归锁）
python3 tools/tests/test_ai_flavor_gold.py
```

v1 全文用扩扫后的检测器复测：反 AI 味 **0 分、11 处命中**——检测器第一次能看见读者看见的东西（此前它只扫开场白+示例对话，Description 与作者的话完全脱检，这正是 91.2 分的来历）。

## 终检法（最有效的一条，工具替代不了）

把 Description 和开场白**读出声**。凡是读到某句心里冒出「这句真妙」，十有八九要改平——**妙感密度就是 AI 浓度**。台词（引号内角色说的话）不受此限，毒舌人设的台词就该扎人；这条只治旁白和介绍文字。

## 与双盲实验的关系

这张卡的 v1/v1.1 后来成为 [LLM 评委双盲实验](blind-review-experiment.md)的 gold 锚点之一——多数 LLM 评委把 v1 判成「更像真人」（它们把细节密度当人味），把真实社区的人类热门卡判成 AI。**别拿 LLM 当人味评委**；本页的病灶清单来自真实读者，这是它比任何自动评委都硬的原因。
