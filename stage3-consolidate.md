# ③ 全书归并

目标：
- 这本书的词表 `games/<slug>/vocab.json`（称呼、组织、地点相关的词，归并和地点都靠它）；
- 全书人物表 `build/characters.json`，分出 S/A/B/C 级；
- 分词词典 `build/userdict.txt`；
- 地图节点 `build/places.json`。

后面的角色档案、知识账本、地图都按这里的人物 id、词表和节点来。

## 工具

| 命令 | 作用 |
|---|---|
| `vocab-suggest --slug S [--key 键 ...] [--top N]` | 从这本书的抽取结果里挖词表候选：每个键给出含义、候选词×分数（带例子），以及词表里已有但这本书用不到的词 |
| `vocab-test --slug S [--spec JSON]` | 当前 `vocab.json`（或 `--spec` 里试的词表）相对框架默认值翻转了哪些名字的“描述词”判断，按出现次数排 |
| `characters --slug S [--no-llm]` | 合并人物、分级，写 `characters.json`。先按规则合并，再把可疑的条目对交给模型裁定（有缓存） |
| `characters-review --slug S` | 模型裁定结果：判为同一人的（yes）、拿不准的（unsure）、判为不同的样例（no） |
| `characters-review --slug S --name X` | 包含名字 X 的人物条目：各章用过的名字、别称、章节范围、台词数、各章描述 |
| `lexicon --slug S` | 生成分词词典，并用书里的真实句子比较加词典前后 |
| `places --slug S [--min-scenes 3]` | 把场景地点归成地图节点，写 `places.json`：节点、上级地点、场景数、章节范围、写法变体；输出覆盖率、根节点数、泛称词、别名 |

## 词表

框架只认识语法级的词（“道”“说”“众人”“那/这”开头的称呼）。“老人”“司机”“经理”“店员”这类称呼、“厂”“局”“公司”这类组织结尾、“房间”“广场”这类泛称地点，都因书而异，由你写进 `vocab.json`。各个键的含义见 `n2g/vocab.py` 的 `KEYS`，`vocab-suggest` 的输出里也有。

1. 跑 `vocab-suggest`。每个键的候选都要看例子再决定，不要整批照收：
   - `descriptor_tails`：只收确实是“某类人”的词（老人、姑娘、经理、店员）。人名的常见结尾（某某儿、某某哥）不收。
   - `organisation_suffixes` / `unique_roles`：组织名的结尾字；每个组织只有一个人担任的头衔（厂长、局长）才进 `unique_roles`。
   - `descriptor_modifiers`：让称呼变成描述的字（颜色、衣着、年龄：“灰衣老人”“小店员”）。不要收姓氏。
   - `kinship` / `people_nouns`：候选是人物表里属于好几个人的名字，要逐个分：亲属称谓和师长称呼进 `kinship`，“客人”“路人”这类通称进 `people_nouns`，这本书里其实只指一个人的写进 `names`。
   - `speech_verbs` / `trailing_speech_verbs`：候选是当前词表核对不到的句式里、名字后面的那个字，混着很多不是说话动词的字（“又”“便”“忙”）。只收真正引出说话的动词。
   - `generic_places` / `dwelling_words` / `place_suffixes`：见下文“地点”。
2. 写进 `vocab.json`，加一行 `note` 说明来源。跑 `vocab-test` 看翻转的名字，随机抽查翻转对不对。
3. 第一轮写完再跑一次 `vocab-suggest`：有了称呼尾词，组织后缀、头衔和修饰字的候选会更准。一般两轮就稳定。
4. 词表改了以后 ③ 以后的阶段都要重跑（`status` 会显示 stale）。

## 合并是怎么做的（了解它才知道该查什么）

1. 同一个专名在不同章里直接合并；描述性称呼（词表里的尾词、指示代词开头、亲属称谓、通称）不跨章合并。
2. 某个别称在全书基本只属于一个人，就把用这个别称当名字的零散条目并进来；两个都已经很大的条目只靠一个别称相连的，不自动合并，交给模型。
3. 名字互相包含、只差一个字、抽取时模型给的全名提示、无名角色在几章后被点名，这几类条目对交给模型，依据各章描述和是否同章并列判断。
4. “甲、乙”“甲等”“甲和乙”这种多人合称不参与合并。
5. 模型是一对一对判断的，两个“是”可能经由第三个条目把两个人串起来。所以合并时按整组检查：两组在同一章里被分别列出两次以上就拒绝合并，记为 `blocked_by_conflict`；`split` 规则也按整组检查。
6. 分级按全书比例（有 `pilot` 时按试点章节）：S 在 ≥20% 的章节出现且台词占 ≥2%；A 在 ≥5% 的章节出现或台词占 ≥1%；B 反复出现或说过话；C 其余；没有台词的角色最多 B 级（没有说话方式可模仿，不写角色卡）。`book.json` 的 `tier_limits`（如 `{"S": 8, "A": 40}`）按戏份限制每级人数，多出来的降一级；`merge_rules.json` 的 `tiers` 手工指定的不受限制。

## 循环

1. 写好词表，跑 `characters`，看 S、A 名单和 `protagonist_guess`。
2. **查 S、A 级每个人**是不是一个人：名字列表里混进了别人，或者同一个人分成了两条。拿不准就 `characters-review --name X` 看各章描述，再用 `grep` 看原文。确认的结论写成金标（`same`，正反例都要有；`tier`）。
3. **看 yes 列表**：逐条看，尤其是两边都出现很多章的。看 unsure：主要角色相关的要人工判断。
4. 把判断写进 `games/<slug>/merge_rules.json`，重跑第 1 步：
   ```json
   {
    "merge": [["名字", "它的错别字", "带姓的全名"]],
    "split": [["甲", "乙"]],
    "generic": ["在这本书里指代不固定的称呼"],
    "proper": ["看起来像描述、其实是固定称呼的名字"],
    "tiers": {"某个名字": "A"}
   }
   ```
   某一类称呼大面积判断错，先改词表；词表表达不了，再写 `is_descriptor` 插件（`plugin-test --hook is_descriptor`）。
5. **主角**：确认 `protagonist_guess` 是原著主角，写进 `book.json` 的 `"protagonist"`。第一人称的书，主角应该就是 ⓪ 定的叙述者。
6. **人数和费用**：S、A 级人数决定 ④ 的卡数。人数明显过多时，先看是不是泛称没进词表；确实是戏份多的书，用 `tier_limits` 控制。
7. 跑 `lexicon`。看 `still_split_after`：如果是因为句子里有更长的词，可以不管；如果是词典漏了或者词条本身是短语，写进 `games/<slug>/lexicon_rules.json` 的 `drop` / `add`。
8. 跑 `gate characters`。

## 地点

`places` 不调用模型。它把场景地点归成节点：去掉方位词（“……门口”“……附近”）；“青石”和“青石城”这种只差一个 `place_suffixes` 结尾的算同一处；以已知地点开头的（“青石城广场”）挂到那个地点下面；“张三处”“张三的房间”（`dwelling_words`）归成“张三住处”；`generic_places` 里的泛称和“结尾出现在好几个地点里的两字词”不当成一个地方，按这一章所在的区域挂成“区域·房间”；场景太少的子地点并进上级。

1. 跑 `places`，看 `scene_share_on_those_nodes`（落在常用节点上的场景比例）、`top`、`generic_words`、`aliases`、`unattached_examples`。
2. 常见问题和落点：
   - 同一个地方好几个节点：一类写法（某个结尾字）就改词表；个别的写进 `place_rules.json` 的 `same`。
   - 泛称成了大节点（“院子”“走廊”）：加进 `generic_places`。
   - 上下级不对：`place_rules.json` 的 `parent`。
   - 不是地点（“梦中”“回忆”）：`ignore`。
   - 别名合错（两个不同的地方被当成同一处）：在 `same` 里把两个都写成自己，就不会再被合并。
   - 场景很少写区域名（整段都在“房间”“走廊”“楼梯”里）：用 `regions` 按章节区间指定区域，区间里的泛称和只在区间里出现的顶层节点都挂到它下面。
   ```json
   {"same": {"变体": "规范名"}, "parent": {"子地点": "父地点"}, "ignore": ["梦中"],
    "regions": [{"name": "青石城", "chapters": [3, 7]}], "note": "依据"}
   ```
3. 重跑，抽查 `top` 里的前 30 个节点，用 `grep` 对原文确认。在 notes.md 记下覆盖率和节点数的变化。

## 验收门槛

- `gate characters` 通过（有 S 级、找到主角、金标全过）。
- S、A 级每一条都人工看过：没有两个人被并成一条，主要角色没有分成两条。
- yes 列表里两边都出现 5 章以上的合并，全部看过。
- 词表的每个键都看过 `vocab-suggest` 的候选，`vocab-test` 的翻转抽查过。
- 分词：`single_token_after` ≥ 0.9，`other_tokens_changed` ≤ 0.02。
- 地点：前 30 个节点抽查过，没有明显的泛称节点和重复节点。
- 在 notes.md 记下：词表来源、合并规则改了几轮、发现了哪些错误合并、最终 S/A 名单、分词和地点的数字。
