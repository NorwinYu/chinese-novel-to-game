"""Stage 2: per-chapter structured extraction with verbatim evidence.

Each chapter is sent to the model with numbered paragraphs and numbered quotations. The model returns
non-story paragraphs, entities, scenes, speaker attribution for every quotation, events and facts with
knowers. A validator checks every reference against the text and sends problems back to the model.
Book-specific guidance written by the agent lives in games/<slug>/extract_guide.md."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from .. import vocab
from ..llm import LLM, BudgetExceeded, LLMError
from ..text.ingest import load_book, load_chapters
from ..text.quotes import find_quotes
SPEAKER_PLACEHOLDERS = {"旁白", "众人", "未知", "unknown"}
HOW = {"said", "thought", "telepathy", "written", "sound", "term", "unknown"}
# Pronouns and bare kinship words are never aliases: they refer to different people from line to line.
PRONOUNS = {"他", "她", "它", "他们", "她们", "它们", "我", "你", "您", "我们", "你们", "咱", "咱们", "自己", "此人", "那人", "这人", "对方", "后者", "前者"}
ENTITY_TYPES = {"person", "group", "place", "item", "technique", "creature", "title", "other"}
KNOW_HOW = {"self", "witnessed", "told", "overheard", "inferred", "public"}
NON_STORY = {"author_note", "ad", "other"}

SYSTEM = """你是小说结构化标注员。你会拿到一章小说，段落已编号为 [p数字]，每处引号里的话已编号为 ⟨q数字⟩。
你的标注会被用来做游戏：每个角色只能知道自己经历过或被告知的事，所以“谁在场、谁说了什么、谁知道什么”必须准确。

输出一个 JSON 对象，字段如下（全部必填，没有就给空数组）：

non_story: 不属于小说正文的段落。[{"p": 段号, "kind": "author_note|ad|other", "note": "简短说明"}]
  作者对读者说的话（求票、请假、感言、预告、聊天）、广告、网站提示。正文段落里夹了一句作者的话也要列出，note 写明是哪句。
  回目、诗词、题记、书信、叙述者的议论和评点都属于小说本身，不要列。拿不准就不列：误删正文比漏标作者的话代价大。

entities: 本章出现的有名有姓或有固定称呼的人物、组织、地点、物品、技能、生物、称号。
  [{"name": "本章最常用的称呼", "type": "person|group|place|item|technique|creature|title|other", "aliases": ["本章里指同一对象的其他称呼，包括“那个老人”“红衣姑娘”这类代称"], "desc": "本章体现的身份、特征、能力、关系，一句话", "p": [出现的段号，最多 8 个]}]
  name 和 aliases 必须逐字出现在正文中。一个对象只列一次。代词（他、她、我、对方、后者等）不算别名。
  普通名词和泛称（人、众人、伙计们、路人）不是实体；只有本章里指固定的一群人、并且作为整体说话或行动时，才作为 group 列出。

scenes: 按时间和地点切分的场景，按顺序覆盖全部正文段落。
  [{"id": "s1", "from": 起始段号, "to": 结束段号, "place": "地点", "time": "时间提示，没有就空", "present": ["在场人物 name"], "summary": "这一场发生了什么，两三句"}]
  present 只算物理上在场或通过某种方式能听见看见的人。

utterances: 每个 ⟨q⟩ 都要标，一个不漏。[["q编号", "说话人 name", ["听话人 name"], "said|thought|telepathy|written|sound|term|unknown"]]
  说话人必须是 entities 里的 name，或“众人”“旁白”“未知”。thought 是心里想的（没人听见），telepathy 是隔空传话、心灵感应之类只有对方能听见的，written 是书信、题字、诗文稿等写下来的文字，sound 是拟声词、叫喊声这类不成句的声音，term 是引号里的称呼、外号、书名、强调的词或叙述者转述的说法（不是有人在这里说出口的话，说话人填“旁白”）。引号里如果混进了叙述（原文引号缺失或方向错），按引号里主要的那句话标。听话人是这句话说给谁听的，不知道就空数组。

events: 推动剧情的事件，按顺序。[{"id": "e1", "scene": "s1", "p": [段号], "summary": "谁做了什么、结果如何", "who": ["参与者 name"], "effects": [{"target": "name", "change": "状态变化：受伤、能力变化、得到或失去物品、关系变化、身份暴露、立下约定等"}], "importance": 1到3}]
  importance 3 是主线大事，2 是本章重要情节，1 是细节。

facts: 读者从本章能确认的、后面可能用得上的信息。[{"id": "f1", "text": "一句完整陈述", "about": ["name"], "kind": "attribute|relationship|backstory|world|plan|secret|state|promise", "p": [段号], "quote": "支撑它的原文，逐字摘录，不超过 40 字", "knowers": [{"who": "name 或 众人", "how": "self|witnessed|told|overheard|inferred|public"}], "secret": true或false}]
  knowers 是到本章结束时，书中谁知道这件事以及怎么知道的，只根据正文判断。叙述者告诉读者但书中人物不知道的，knowers 只列确实知道的人；secret 为 true 表示有人刻意隐瞒或多数人不知道。
  剧情细节都要记：能力和实力、拥有的物品、伤势、身份、亲属和师生关系、恩怨、承诺和约定、计划、秘密、规矩和设定、时间节点。比喻和景物描写不用记。

要求：
- 只根据给出的正文，不要用你对这本书的已有印象补充正文没写的内容。
- 段号、q 编号必须是给出的编号。quote 必须能在对应段落里逐字找到。
- 写 JSON 前先通读全章。"""

NARRATOR_GUIDE = """本书是第一人称叙述。引号外叙述里的“我”是 {name}。“我”的在场、经历、行动和知道的事都记在 name “{name}” 下：entities 里列出 {name}（本章正文没出现这个名字也要列），scenes.present、events.who、facts.about 和 knowers 里写 “{name}”，不要写“我”。“我说：……”引出的话，说话人是 {name}。引号里的“我”指说话人自己，不是叙述者。"""


def narrator_for(profile: dict | None, idx: int) -> str | None:
    """The first-person narrator of chapter idx from book.json profile, or None for third-person text.
    profile.narrator is a name, or {"a-b": name} for books whose narrator changes (chapters a..b
    inclusive, like stage ranges; "a-" runs to the end)."""
    profile = profile or {}
    if profile.get("person") not in ("first", "mixed"):
        return None
    who = profile.get("narrator") or "叙述者"
    if isinstance(who, dict):
        for spec, name in who.items():
            a, _, b = spec.partition("-")
            if int(a or 0) <= idx <= (int(b) if b else 10 ** 9):
                return name
        return None
    return who


def number_chapter(paragraphs: list[str]) -> tuple[str, dict[str, tuple[int, str, int]]]:
    """Numbered text plus {qid: (paragraph, quotation, start offset in paragraph)}."""
    quotes: dict[str, tuple[int, str, int]] = {}
    lines = []
    for i, p in enumerate(paragraphs):
        out, last = [], 0
        for a, b in find_quotes(p):
            qid = f"q{len(quotes)}"
            quotes[qid] = (i, p[a:b], a)
            out.append(p[last:a] + f"⟨{qid}⟩" + p[a:b])
            last = b
        out.append(p[last:])
        lines.append(f"[p{i}] " + "".join(out))
    return "\n".join(lines), quotes


# "<name>……道：“" right before a quotation, or "”<name>……道。" right after. The speech verbs come from
# n2g.vocab (ordinary Chinese, with per-book additions).
_CLAUSE_BREAK = re.compile(r"[，,。！？!?；;”」…]")
_SENT_END = re.compile(r"[。！？!?“”「」]")
_REACTION = re.compile(r".{0,2}[听见闻]")
_NOT_SUBJECT_AFTER = ("之", "的", "身旁", "身边", "身后", "身前", "旁边", "面前", "跟前", "那里", "这里")
_LEAD_WORDS = "那这又便忙因却"


def _clause_subject(clause: str, names: dict[str, str]) -> tuple[str, int] | None:
    """(canonical name, end offset) of the name opening the clause. Names later in the clause are
    usually objects (嘱张三道, 对着老人道) with the real subject elided; "X之妻Y" / "X身旁" name
    someone else."""
    clause = clause.lstrip()
    best: tuple[int, str] | None = None
    for alias, canon in names.items():
        # one-character aliases are too ambiguous, except the narrator's 我 when the book maps it
        if len(alias) < 2 and alias != "我":
            continue
        if clause.startswith(alias):
            pos = 0
        elif clause.startswith(alias, 1) and clause[0] in _LEAD_WORDS:
            pos = 1
        else:
            continue
        if clause.startswith(_NOT_SUBJECT_AFTER, pos + len(alias)) or (alias == "我" and clause.startswith("们", pos + 1)):
            continue
        if best is None or pos + len(alias) > best[0]:
            best = (pos + len(alias), canon)
    return (best[1], best[0]) if best else None


def cue_speaker(paragraph: str, quote_start: int, names: dict[str, str], window: int = 30) -> str | None:
    """Canonical name of the subject of the clause that introduces the quotation with a speech verb
    ("王五笑指向李四道：" -> 王五). None when the clause has no subject we know."""
    before = paragraph[max(0, quote_start - window): quote_start]
    if not vocab.current.speech_cue.search(before):
        return None
    hit = _clause_subject(_CLAUSE_BREAK.split(before)[-1], names)
    return hit[0] if hit else None


def trailing_cue_speaker(paragraph: str, quote_start: int, quote_end: int, names: dict[str, str]) -> str | None:
    """Speaker named right after the quotation in a sentence that ends with a speech verb
    ("”张三缓缓站起身子，嘴角噙着嘲讽，笑问道，" -> 张三): the first clause opening with a known name."""
    if paragraph[:quote_start].rstrip().endswith(("：", ":")):
        return None  # introduced before the quotation; what follows is the next action
    sentence = _SENT_END.split(paragraph[quote_end:quote_end + 60], 1)[0]
    if "：" in sentence or ":" in sentence:
        return None  # these words introduce the next quotation
    clauses = [c.strip() for c in _CLAUSE_BREAK.split(sentence) if c.strip()]
    if not any(vocab.current.trail_verb.search(c) for c in clauses):
        return None
    for c in clauses:
        hit = _clause_subject(c, names)
        if hit:
            # "X听了" / "X见问" is the listener reacting, not the speaker
            return None if _REACTION.match(c, hit[1]) else hit[0]
        if vocab.current.trail_verb.search(c):
            return None
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _letters(s: str) -> str:
    """Only characters that carry words: quotes copied with different punctuation or invisible
    characters (zero-width spaces are common in web texts) still count as verbatim."""
    return "".join(ch for ch in s if ch.isalnum())


def _apply_rename(obj: dict, rename: dict[str, str]) -> None:
    r = lambda x: rename.get(x, x)
    for s in obj["scenes"]:
        s["present"] = [r(x) for x in s.get("present", [])]
    for u in obj["utterances"]:
        if isinstance(u, list) and len(u) >= 2:
            u[1] = r(u[1])
            if len(u) > 2 and isinstance(u[2], list):
                u[2] = [r(x) for x in u[2]]
    for e in obj["events"]:
        e["who"] = [r(x) for x in e.get("who", [])]
        for eff in e.get("effects", []):
            eff["target"] = r(eff.get("target", ""))
    for f in obj["facts"]:
        f["about"] = [r(x) for x in f.get("about", [])]
        for k in f.get("knowers", []):
            k["who"] = r(k.get("who", ""))


def make_validator(paragraphs: list[str], quotes: dict[str, tuple[int, str, int]], *, recheck: bool = True,
                   narrator: str | None = None):
    n = len(paragraphs)
    full = "".join(paragraphs)
    norm_paras = [_norm(p) for p in paragraphs]
    letter_paras = [_letters(p) for p in paragraphs]

    asked: set[str] = set()  # cue mismatches already put to the model; its answer stands

    def valid_p(x) -> bool:
        return isinstance(x, int) and 0 <= x < n

    def validate(obj):
        problems: list[str] = []
        if not isinstance(obj, dict):
            return obj, ["顶层必须是 JSON 对象"]
        for key in ("non_story", "entities", "scenes", "utterances", "events", "facts"):
            if not isinstance(obj.get(key), list):
                obj[key] = []
                problems.append(f"缺少数组字段 {key}")

        for ns in obj["non_story"]:
            if not valid_p(ns.get("p")):
                problems.append(f"non_story 段号无效：{ns.get('p')}")
            elif ns.get("kind") not in NON_STORY:
                ns["kind"] = "other"

        names: set[str] = set(SPEAKER_PLACEHOLDERS) | ({narrator} if narrator else set())
        rename: dict[str, str] = {"我": narrator} if narrator else {}
        for e in obj["entities"]:
            e.pop("unverified", None)
            name = e.get("name", "")
            e["aliases"] = [a for a in e.get("aliases", []) if a in full and a not in PRONOUNS and a != name
                            and not a.endswith("的")]
            if narrator and name != narrator and e.get("outside_name") == narrator:
                # an earlier pass renamed the narrator to the chapter's way of addressing them; undo it
                e["aliases"] = [name, *e["aliases"]]
                e.pop("outside_name")
                rename[name] = narrator
                name = e["name"] = narrator
            if name and name not in full and e["aliases"] and name != narrator:
                # The narrator keeps the profile name even where the chapter only says 我 or a nickname.
                # The model knows the book and likes full names the chapter never uses (张小三 for 小三).
                # Keep the chapter's own wording as name; the outside name is an unverified hint for merging.
                new = max(e["aliases"], key=full.count)
                e["aliases"].remove(new)
                e["outside_name"] = name
                rename[name] = new
                name = e["name"] = new
            if not name or (name not in full and name != narrator):
                e["unverified"] = True
                problems.append(f"实体 {name!r} 没有逐字出现在正文中")
            if e.get("type") not in ENTITY_TYPES:
                problems.append(f"实体 {name} 的 type 无效：{e.get('type')}")
            names.add(name)
            names.update(e.get("aliases", []))
            e["p"] = [p for p in e.get("p", []) if valid_p(p)]
        if rename:
            _apply_rename(obj, rename)

        def check_name(x: str, where: str) -> None:
            if x in names:
                return
            if x and x in full and x not in PRONOUNS:
                obj["entities"].append({"name": x, "type": "other", "aliases": [], "desc": "", "p": [], "auto_added": True})
                names.add(x)
                return
            problems.append(f"{where} 里的 {x!r} 不是 entities 的 name 或别名，也没有出现在正文中；请改用 entities 里的 name")

        last_to = -1
        for s in obj["scenes"]:
            a, b = s.get("from"), s.get("to")
            if not (valid_p(a) and valid_p(b) and a <= b):
                problems.append(f"场景 {s.get('id')} 的段号范围无效：{a}-{b}")
                continue
            if a <= last_to:
                problems.append(f"场景 {s.get('id')} 与上一个场景重叠（从 p{a} 开始，上一场到 p{last_to}）")
            last_to = b
            for x in s.get("present", []):
                check_name(x, f"场景 {s.get('id')} 的 present")
        if obj["scenes"] and last_to < n - 1:
            tail = "".join(paragraphs[last_to + 1:])
            if len(tail) > 200:
                problems.append(f"场景没有覆盖到最后（最后一场到 p{last_to}，全章到 p{n - 1}）")

        seen_q: set[str] = set()
        cleaned = []
        for u in obj["utterances"]:
            if not (isinstance(u, list) and len(u) >= 2):
                problems.append(f"utterances 的条目格式应为 [q编号, 说话人, [听话人], 方式]：{u}")
                continue
            qid, speaker = u[0], u[1]
            to = u[2] if len(u) > 2 and isinstance(u[2], list) else []
            how = u[3] if len(u) > 3 else "said"
            if qid not in quotes:
                problems.append(f"不存在的引号编号 {qid}")
                continue
            if qid in seen_q:
                continue
            seen_q.add(qid)
            check_name(speaker, f"{qid} 的说话人")
            for x in to:
                check_name(x, f"{qid} 的听话人")
            if how not in HOW:
                how = "unknown"
            cleaned.append([qid, speaker, to, how])
        obj["utterances"] = cleaned
        canon = {e["name"]: e["name"] for e in obj["entities"] if e.get("type") in ("person", "creature", "group", "other")}
        if narrator:
            canon["我"] = narrator
        for e in obj["entities"]:
            if e.get("type") in ("person", "creature", "group", "other"):
                for a in e.get("aliases", []):
                    canon.setdefault(a, e["name"])
        mismatches = []
        for u in cleaned:
            if u[3] not in ("said", "unknown"):
                continue
            p, body, start = quotes[u[0]]
            assigned = canon.get(u[1], u[1])
            cue = cue_speaker(paragraphs[p], start, canon)
            if cue and cue != assigned:
                ctx = paragraphs[p][max(0, start - 30): start + 12]
                mismatches.append((u[0], f"{u[0]} 前文是“{ctx}”，引出这句话的像是 {cue}，你标的是 {u[1]}"))
                continue
            cue = trailing_cue_speaker(paragraphs[p], start, start + len(body), canon)
            if cue and cue != assigned:
                ctx = paragraphs[p][max(0, start + len(body) - 12): start + len(body) + 30]
                mismatches.append((u[0], f"{u[0]} 后文是“{ctx}”，说这句话的像是 {cue}，你标的是 {u[1]}"))
        obj["cue_mismatches"] = [m for _, m in mismatches]
        new = [(q, m) for q, m in mismatches if q not in asked]
        if recheck and new:
            asked.update(q for q, _ in new)
            problems.append("请核对以下说话人（如果你确认原标注正确，保持不变即可）：\n  " + "\n  ".join(m for _, m in new[:30]))
        missing = [q for q in quotes if q not in seen_q]
        if missing:
            problems.append(f"这些引号没有标注说话人：{', '.join(missing[:60])}")

        for e in obj["events"]:
            e["p"] = [p for p in e.get("p", []) if valid_p(p)]
            if not e["p"]:
                problems.append(f"事件 {e.get('id')} 没有有效段号")
            for x in e.get("who", []):
                check_name(x, f"事件 {e.get('id')} 的 who")
            for eff in e.get("effects", []):
                check_name(eff.get("target", ""), f"事件 {e.get('id')} 的 effects.target")

        for f in obj["facts"]:
            f.pop("unverified", None)
            quote = _norm(f.get("quote", ""))
            ps = [p for p in f.get("p", []) if valid_p(p)]
            if not quote:
                f["unverified"] = True
                problems.append(f"事实 {f.get('id')} 缺少 quote")
            elif not any(quote in norm_paras[p] for p in ps):
                found = [i for i, t in enumerate(norm_paras) if quote in t]
                if not found and len(_letters(quote)) >= 4:
                    found = [i for i, t in enumerate(letter_paras) if _letters(quote) in t]
                if found:
                    ps = sorted(set(ps) | {found[0]})
                else:
                    f["unverified"] = True
                    problems.append(f"事实 {f.get('id')} 的 quote 在正文中找不到（必须逐字摘录）：{f.get('quote', '')[:40]}")
            f["p"] = ps
            for x in f.get("about", []):
                check_name(x, f"事实 {f.get('id')} 的 about")
            for k in f.get("knowers", []):
                check_name(k.get("who", ""), f"事实 {f.get('id')} 的 knowers")
                if k.get("how") not in KNOW_HOW:
                    k["how"] = "inferred"
        return obj, problems

    return validate


def load_guide(book: Path) -> str:
    path = book / "extract_guide.md"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


async def extract_chapter(llm: LLM, chapter: dict, *, guide: str = "", prev_tail: str = "",
                          narrator: str | None = None) -> dict:
    text, quotes = number_chapter(chapter["paragraphs"])
    system = SYSTEM + ("\n\n" + NARRATOR_GUIDE.format(name=narrator) if narrator else "") + \
        (f"\n\n关于这本书的补充说明（由整理者根据全书写的，用来统一称呼和判断）：\n{guide}" if guide else "")
    user = (f"上一章结尾（只作上下文，不要标注）：\n{prev_tail}\n\n" if prev_tail else "") + \
           f"本章：{chapter['heading']}\n\n{text}"
    budget = min(32000, 4000 + 60 * len(quotes) + len(text) // 2)
    history: list[list[str]] = []
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    validate = make_validator(chapter["paragraphs"], quotes, narrator=narrator)
    try:
        obj, problems = await llm.chat_json("extract", messages, validate=validate, max_tokens=budget,
                                            temperature=0.1, history=history)
    except LLMError as e:
        if "truncated" not in str(e):
            raise
        history.append([f"输出超长被截断（max_tokens={budget}），放宽后重做"])
        obj, problems = await llm.chat_json("extract", messages, validate=validate, max_tokens=budget * 2,
                                            temperature=0.1, history=history)
    return {"idx": chapter["idx"], "heading": chapter["heading"], "paragraphs": len(chapter["paragraphs"]),
            "quotes": len(quotes), "attempts": len(history), "attempt_problems": history,
            "result": obj, "problems": problems}


def revalidate_book(book: Path) -> dict:
    """Re-run the current validator over stored results without calling the model, after the
    validator itself improved. Problems and unverified flags are recomputed."""
    chapters = load_chapters(book)
    profile = load_book(book).get("profile")
    vocab.use_book(book)
    before = after = 0
    for path in sorted((book / "build" / "extract").glob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if not rec.get("result"):
            continue
        paras = chapters[rec["idx"]]["paragraphs"]
        _, quotes = number_chapter(paras)
        before += len(rec.get("problems") or [])
        rec["result"], problems = make_validator(paras, quotes, recheck=False, narrator=narrator_for(profile, rec["idx"]))(rec["result"])
        rec["problems"] = problems
        after += len(problems)
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"problems_before": before, "problems_after": after}


def out_path(book: Path, idx: int) -> Path:
    return book / "build" / "extract" / f"{idx:05d}.json"


async def extract_book(book: Path, llm: LLM, indices: list[int], *, force: bool = False) -> list[dict]:
    chapters = load_chapters(book)
    guide = load_guide(book)
    profile = load_book(book).get("profile")
    vocab.use_book(book)

    def stored(i: int) -> dict | None:
        path = out_path(book, i)
        if path.exists() and not force:
            rec = json.loads(path.read_text(encoding="utf-8"))
            if "error" not in rec:  # failed chapters are retried on the next run
                return rec
        return None

    todo = [i for i in indices if stored(i) is None]
    llm.job("chapters", len(todo))

    async def one(i: int) -> dict:
        rec = stored(i)
        if rec is not None:
            return rec
        prev = chapters[i - 1]["paragraphs"][-3:] if i > 0 else []
        try:
            rec = await extract_chapter(llm, chapters[i], guide=guide, prev_tail="\n".join(prev)[-600:],
                                        narrator=narrator_for(profile, i))
        except BudgetExceeded as e:
            return {"idx": i, "heading": chapters[i]["heading"], "error": f"BudgetExceeded: {e}"}
        except Exception as e:  # keep going; the report lists failures
            rec = {"idx": i, "heading": chapters[i]["heading"], "error": f"{type(e).__name__}: {e}"}
        llm.tick()
        path = out_path(book, i)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return rec

    return await asyncio.gather(*(one(i) for i in indices))
