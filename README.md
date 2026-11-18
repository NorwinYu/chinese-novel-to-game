# chinese-novel-to-game

**把一本中文小说 TXT，做成能玩的网页游戏。**
Turn a Chinese novel (TXT) into a playable, self-hosted web game.

不是“一句话让大模型写个游戏”。这是一条**可核查的流水线**：清洗切章 → 逐章结构抽取 → 人物归并 → 知识账本 → 分阶段角色卡 → 游戏化 → 玩法设计 → 3D 画面 → 验收。每个阶段都有量化门槛和人工核对过的金标；NPC 只能知道自己当时知道的事，玩家偏离原著时由旁白软回归。

仓库**不包含任何小说原文或改编产物**，只包含框架代码、给智能体用的作业手册，以及一段自写的示例文本。**请只使用你有权使用的文本**：这是技术研究项目，不提供、也不协助获取受版权保护的作品；完整条款见 [NOTICE.md](NOTICE.md)。

介绍页（架构与流水线图解）：<https://norwinyu.github.io/chinese-novel-to-game/>

---

## 它能做出什么

- **阅读**：按原著正史逐场回放，对白标注说话人，场景切换带动 3D 画面和地图。
- **抉择**：每个关键情节点一个抉择；原著做法是 canon，其余是处境下合理的别的做法，各有得失、后果旁白，并用一段 `bridge` 接回原著下一场。
- **经营**：2–4 个属性（用书里的单位和数目）、一个阶段目标、每章末的空闲行动、失败结局。
- **情报**：找 NPC 打听，对方真的说出来了才解锁之后某个抉择里更好的选项。
- **对话**：NPC 由 AI 扮演，人设来自分阶段角色卡，记忆来自账本，只知道自己经历或被告知的事；带表情、好感、动作。
- **画面**：默认浏览器实时渲染的低多边形 3D（three.js 随仓库附带，离线可用）；有本地图像服务时可换成 AI 图，属于可选项。
- **可玩性有客观判据**：照原著走必须活到最后（`canon_survives`）、会玩的人必须能赢（`win_reachable`）、乱玩必须可能输（`lose_reachable`）。

## 快速开始

```bash
git clone https://github.com/NorwinYu/chinese-novel-to-game.git
cd chinese-novel-to-game
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # 填 N2G_LLM_BASE_URL / N2G_LLM_API_KEY / N2G_MODEL_DEFAULT
```

任何 OpenAI 兼容的 `/chat/completions` 接口都可以；模型要能稳定输出 JSON。

先用仓库自带的示例文本跑通 ⓪①（不花钱）：

```bash
python -m n2g init examples/demo-novel.txt --slug demo --title 示例
python -m n2g ingest --slug demo
python -m n2g profile --slug demo
```

换成自己的书，就是把 `examples/demo-novel.txt` 换成你的 TXT。**长篇请先做切片**：在 `games/<slug>/book.json` 写 `"pilot": "0:30"`，只在 20~30 章上走通全流程，估好费用再放开。

完整流程（每步之后跑一次 `gate`，过了再往下）：

```bash
python -m n2g extract   --slug demo --chapters 0:30 --budget 1
python -m n2g characters --slug demo
python -m n2g lexicon    --slug demo
python -m n2g places     --slug demo
python -m n2g ledger     --slug demo
python -m n2g cards      --slug demo --tiers S
python -m n2g game       --slug demo
python -m n2g design     --slug demo --budget 0.2
python -m n2g looks      --slug demo
python -m n2g probe      --slug demo --budget 0.1
```

玩：

```bash
python -m n2g play --slug demo --port 8765     # http://127.0.0.1:8765/
```

`play` 需要先有 `build/design.json`（⑥b）。没有玩法设计时页面会提示先跑 `design`。

### 哪些阶段需要 API key 和额度

只有下表中标了「小额 / 中额 / 主要开销」的步骤会发起付费请求，其余步骤全部离线、免费。带 `--budget` 的命令到达上限后停止发起新请求，已完成的结果保留；模型调用按请求体哈希缓存，提示词不变不重复付费。

| 阶段 | 命令 | 额度 | 说明 |
|---|---|---|---|
| ⓪ 书型诊断 | `profile` | 免费 | 纯统计 |
| ① 清洗切章 | `ingest` `noise-test` `evidence` | 免费 | 纯规则；可选的 `noise-scan` 用模型 |
| ② 逐章抽取 | `extract` | **主要开销** | 每章一次调用；先 `--chapters 0:30` 试点，再放开全书 |
| ③ 全书归并 | `characters` | 小额 | 只把可疑条目对交给模型；`--no-llm` 完全免费 |
| ③ 分词 / 地图 | `lexicon` `places` | 免费 | 纯统计 |
| ⑤ 知识账本 | `ledger` `rules-test` | 免费 | 不调模型，几秒重建 |
| ④ 角色档案 | `cards` | 中额 | 每张卡一次调用；先 `--dry-run` 估价，或 `--tiers S` 收窄 |
| ⑥ 游戏化 | `game` | 免费 | 纯计算 |
| ⑥b 玩法设计 | `design` | 小额 | frame / intel / 每个锚点各一次；`--check` 免费 |
| ⑦ 画面 | `looks` | 小额 | 每个物件一次调用；`art render` 走本地图像服务，不占文本额度 |
| ⑧ 验收 | `probe` | 小额 | 每条探针一次对话 |
| 游玩 | `talk` `play` | 按对话计 | 每说一句一次调用；`play` 启动本身不花钱 |

完全不调用模型的命令：`gate` `status` `golden` `eval` `extract --revalidate` `extract-review` `cards --dry-run` `cards --review` `design --check` `characters --no-llm` `ingest` `profile` `evidence` `grep` `show` `anomalies` `long-chapters` `noise-test` `plugin-test` `vocab-suggest` `vocab-test` `rules-test` `places` `lexicon` `ledger` `game` `timeline` `art sheet`。

一句话：**⓪① 免费，② 是主要花费，③④⑥b⑦⑧ 是小额，⑥ 与账本免费**。

## 设计架构

### 三层分工

| 层 | 位置 | 放什么 |
|---|---|---|
| 框架 | `n2g/` | 对所有中文小说都成立的机制：编码识别、切章、抽取提示词与校验、人物归并、账本、角色卡、引擎、NPC 运行时、3D 渲染、门槛。**不含任何一本书的词、名、阈值** |
| 每本书 | `games/<slug>/` | 这本书的书型、词表、噪声/合并/知情/地点规则、抽取说明、插件、人工金标、笔记；`build/` 下是全部产物 |
| 作业手册 | `SKILL.md` + `stage*.md` + `patterns.md` | 给智能体（或人）的操作规程：每阶段怎么跑、看哪个指标、抽查什么、问题修在哪一层 |

框架只认语法级的词（“道”“说”“众人”）。题材、年代、称呼体系、地名写法一律由每本书的规则文件表达；规则表达不了的，才在 `games/<slug>/plugins/` 写十几行 Python。

### 流水线

```
TXT
 │ ⓪ profile          书型诊断：人称、引号、换行、篇幅 → book.json.profile
 ▼
chapters.jsonl
 │ ① ingest           编码/噪声/乱码修复/切章/编号审计
 ▼
build/extract/*.json
 │ ② extract          每章：非正文段落、实体、场景在场、逐句说话人、事件、事实+知情人
 │                    （每条事实必须带逐字原文，校验不过退回重做）
 ▼
characters.json  userdict.txt  places.json
 │ ③ characters/lexicon/places   别名合并、S/A/B/C 分级、分词词典、地图节点
 ▼
ledger.json
 │ ⑤ ledger           每条事实/事件：谁在第几章知道（不调模型，几秒重建）
 ▼
build/cards/<id>.json
 │ ④ cards            每个主要角色每个剧情阶段一张卡（身份/性格/说话方式/关系/秘密）
 ▼
build/game.json
 │ ⑥ game             beats（每场一屏）、锚点、地图、人物行踪
 ▼
build/design.json
 │ ⑥b design          属性/目标/空闲行动/情报/每个锚点一个抉择/结局 + 离线模拟调数值
 ▼
build/looks.json + 可选 AI 图
 │ ⑦ looks / art      3D 参数（枚举值，可校验）；有本地图像服务时出图
 ▼
build/probe_report.json
    ⑧ probe + play     剧透/记忆探针 + 浏览器里真玩一遍
```

阶段顺序上 ⑤ 在 ④ 之前：角色卡依赖账本里的“谁在场、谁知道”。

### 几个关键设计

**1. 证据优先。** 抽取出的实体名必须在本章原文出现；每条事实必须附逐字引文；每个引号必须有说话人。校验失败会把问题清单发回模型重做，最多三轮；仍不过的标 `unverified`，下游（账本、角色卡、NPC 记忆）不把它当证据。

**2. 门槛 + 金标 + 过期检测。** `gate` 不只记“通过”，还记下该阶段输入文件的指纹。改了规则、词表、插件或代码后，`status` 会标出哪些阶段 `stale`：已经“通过”的结论不算数，要重跑。金标（`golden.jsonl`）是人工对照原文确认过的判断——这句谁说的、这两个名字是不是一个人、某人某章在不在场、知不知道某事——每次重建自动复查，回归不会悄悄发生。

**3. 修复落点表。** 出了问题先判断属于哪一层，写在**最上面**能解决它的地方：

| 层 | 位置 |
|---|---|
| 1 书型 | `book.json` 的 `profile` |
| 2 词表 | `games/<slug>/vocab.json` |
| 3 规则文件 | `noise_rules.json` / `merge_rules.json` / `knowledge_rules.json` / `place_rules.json` |
| 4 抽取说明 | `extract_guide.md` |
| 5 插件 | `games/<slug>/plugins/*.py` |
| 6 框架 | `n2g/`（同一类问题在**两本以上**书里出现才改） |

**4. 通用性由测试强制。** `tests/test_generic.py` 拿已构建的书的主要人名、别名、常用地点反向扫描 `n2g/`、测试和文档；往框架里写某本书的词会被测出来。

**5. 玩法可验证。** `sim.py` 与网页共用同一个引擎，跑三类玩家：永远选原著（必须走到最后，且原著里的大损失不能被属性下限截掉）、按权重规划（必须能赢）、随机（必须可能输）。数值不对就让模型改，最多三轮，保留最好的一版。

**6. NPC 不剧透是“先决定他能看到什么”。** 回答前只给模型四样东西：上一个**已结束**阶段的角色卡（当前阶段的卡会剧透结局）、账本里他此刻知道的事实和事件（按与玩家这句话的相关度排序）、当前场景和玩家已读原文、玩法层的好感与情报。回答后再校验一次：说出了玩家没提、且首次出现章节更晚的名字，就打回重写。`talk --show-context` 可以打印“这个人此刻能看到的一切”，用来判断剧透是上游材料错还是模型发挥。

**7. 省钱。** 所有付费命令支持 `--budget`（或 `N2G_BUDGET_USD`），到额度就不再发起新请求，已完成的结果保留；模型调用按请求体哈希落盘缓存，提示词不变不重复付费；`cards --dry-run` 先估价。

## 目录结构

```
.
├── SKILL.md                 # 技能入口：工作原则、落点表、阶段总览（智能体从这里读）
├── NOTICE.md                # 法律声明：技术研究项目，只处理你有权使用的文本
├── stage0…stage8*.md        # 每阶段的详细做法、指标与门槛
├── patterns.md              # 问题模式库：症状 → 确认 → 修在哪层
├── n2g/
│   ├── cli.py               # 全部命令
│   ├── llm.py               # OpenAI 兼容客户端：缓存、重试、预算、JSON 修复
│   ├── config.py            # .env / 每本书目录
│   ├── gates.py golden.py   # 阶段门槛、金标
│   ├── profile.py vocab.py  # 书型诊断、词表
│   ├── text/                # 清洗、切章、引号、噪声、检查工具
│   ├── extract/             # 逐章抽取、校验、复审
│   ├── consolidate/         # 人物合并、分词词典
│   ├── knowledge/           # 角色卡、知识账本、地点、知情规则
│   ├── game/                # 引擎、NPC 运行时、玩法设计、模拟、3D、服务器
│   │   └── static/          # 前端（three.js 随仓库附带）
│   └── art/                 # 可选：AI 图
├── tests/                   # 38 个离线测试（没有书时其中 1 个自动跳过）
├── examples/demo-novel.txt  # 自写的 5 章示例（CC0）
├── requirements.txt
└── .env.example
```

一本书跑完后，`games/<slug>/` 长这样：

```
games/<slug>/
├── book.json          # 书型、阶段划分、主角、切片范围、美术设定
├── vocab.json         # 这本书的称呼/组织/地点词
├── *_rules.json       # 噪声、合并、知情、地点规则
├── extract_guide.md   # 给抽取模型的这本书专用说明
├── plugins/*.py       # 规则表达不了的逻辑
├── golden.jsonl       # 人工核对过的判断
├── notes.md           # 每个决定的依据、每轮修复的结果
└── build/             # 全部产物（已 gitignore）
```

## 命令速查

| 命令 | 作用 |
|---|---|
| `init <txt> --slug S` | 登记一本书 |
| `ingest` / `evidence` / `profile` | 清洗切章 / 统计与抽样 / 书型诊断 |
| `noise-scan` / `noise-test` | 噪声规则草稿 / 全书测试 |
| `grep` / `show` / `anomalies` / `long-chapters` | 查原文、看章节、编号异常、长章检查 |
| `extract` / `extract-review` / `extract --revalidate` | 逐章抽取 / 汇总与单章对照 / 免费重校验 |
| `characters` / `characters-review` / `lexicon` | 人物合并分级 / 合并裁定 / 分词词典 |
| `places` | 场景地点 → 地图节点 |
| `ledger` / `rules-test` | 知识账本 / 知情规则在全书的测试 |
| `cards` / `timeline` | 角色卡（`--dry-run` 估价）/ 事件时间线 |
| `game` / `design` / `talk` / `play` | 游戏骨架 / 玩法设计与模拟 / 命令行对话 / 浏览器游玩 |
| `looks` / `art` | 3D 参数 / 可选 AI 图（`plan`/`render`/`sheet`） |
| `probe` | 剧透、记忆探针 |
| `gate` / `status` / `golden` / `eval` | 阶段门槛 / 门槛与过期总览 / 金标增删查 / 复查 |
| `plugin-test` / `run` | 插件全书测试 / 跑自己的脚本 |

`python -m n2g -h` 列出全部；`python -m n2g <cmd> -h` 看单个命令。

## 安装成一个技能

`SKILL.md` 在仓库根目录，`stage*.md` 与它同级。把整个仓库（或它的软链）放进你的 agent 运行时的 skills 目录即可：

```bash
ln -s "$PWD" ~/.agents/skills/chinese-novel-to-game
```

不同运行时的目录不同，认 `SKILL.md` 的 frontmatter（`name` / `description`）即可。`description` 里写了触发场景：用户给小说文件、要求把小说改成游戏、或要求继续某本书的构建。

## 开发

```bash
pip install -r requirements.txt
pytest -q
```

测试全部离线，不读 `.env`，不需要模型。改框架前先读 `SKILL.md` 的落点表：多数需求应该落在书目录，而不是 `n2g/`。

## 法律声明与使用边界

**这是一个技术研究项目**：研究对象是文本处理与受约束的生成流程（结构化抽取、实体归并、知识状态追踪、离线可验证的玩法模拟），不是任何具体作品。仓库**不含**任何受版权保护的小说原文、角色设定、插图或语料；示例文本 `examples/demo-novel.txt` 为自写原创，按 CC0 释放。

- **不要下载、复制或改编未授权的作品。** 本项目的用途不是获取文本，而是处理你已经有权使用的文本；从盗版或未授权渠道获取全文小说，无论理由是"自己玩"还是"做研究"，都可能构成侵权。
- 工具只读取你放在本机的 TXT 文件，不含抓取、下载、破解或规避技术保护措施的功能。
- 请**只处理你有权使用的文本**：已进入公有领域的作品、你本人创作的作品，或你已获权利人授权的作品。
- `games/<slug>/build/` 下的产物含有或派生自你输入的文本，发布、分享或商用它们与使用原作品受同样的约束。
- 本项目不提供法律意见，作者不对你的使用承担任何责任。完整条款见 [NOTICE.md](NOTICE.md)。

## 隐私

- `.env` 与任何 `.env.*`（除 `.env.example`）都被 gitignore；密钥只从环境变量读，不写进产物。
- 所有模型调用只发往你在 `N2G_LLM_BASE_URL` 配置的地址；没有遥测、没有回连。
- `play` 只监听 `127.0.0.1`，不对外网开放；存档在 `build/saves/`。
- `games/*/build/`、`.cache/`、`build/llm_cache/` 都被 gitignore：它们含有原文片段和模型输出。

## 已知限制

- 抽取只认引号里的对白。全书对白不带引号的书，需要先写 `repair_line` 插件把对白标出来。
- 账本按章计时，“在场即知道事件”。某人当面被瞒着的情节会记错，用金标 `knows`（`expect: false`）+ `known_gap` 标注；角色卡的泄漏检查会兜住一部分。
- 角色卡描述的是每个阶段**末尾**的状态；阶段划分要尽量让关键人物的退场落在边界上。
- 长书很贵：上千章的书先做 10 章左右、8–15 个锚点的切片，认可后再扩大。
- 只支持 OpenAI 兼容的文本接口。句向量记忆排序（`N2G_EMBED_MODEL`）和 AI 图（`N2G_IMAGE_URL`）都是可选增强，缺省下 BM25 + 3D 完全可用。

## License

MIT，见 [LICENSE](LICENSE)。
