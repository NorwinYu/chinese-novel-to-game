"""Async OpenAI-compatible chat client with disk cache, retries, JSON repair and usage accounting."""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from .config import Settings, get_settings


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    """Raised before a new paid call once the run's spending reached its cap; work already saved stays."""

@dataclass
class Usage:
    calls: int = 0
    cached_calls: int = 0
    prompt_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    seconds: float = 0.0
    list_cost: float = 0.0   # gateway-reported price of the calls, when the provider reports it
    allowance: float = 0.0   # share of a subscription allowance used, when reported

    def add(self, usage: dict, seconds: float) -> None:
        self.calls += 1
        self.prompt_tokens += usage.get("prompt_tokens", 0)
        self.prompt_cache_hit_tokens += usage.get("prompt_cache_hit_tokens",
                                                  (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        # some gateways nest their price next to the token counts; anything with these fields counts
        gateway = next((v for v in usage.values() if isinstance(v, dict)
                        and ("list_cost" in v or "allowance" in v)), {})
        self.list_cost += gateway.get("list_cost", usage.get("cost", 0)) or 0
        self.allowance += gateway.get("allowance", 0) or 0
        self.completion_tokens += usage.get("completion_tokens", 0)
        self.reasoning_tokens += (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
        self.seconds += seconds

    def cost(self, price: tuple[float, float, float] | None = None) -> float:
        """Gateway-reported cost, or a token estimate from (input, output, cached-input) USD per million."""
        if self.list_cost or not price:
            return self.list_cost
        fresh = self.prompt_tokens - self.prompt_cache_hit_tokens
        return (fresh * price[0] + self.completion_tokens * price[1] + self.prompt_cache_hit_tokens * price[2]) / 1e6

    def to_dict(self) -> dict:
        return asdict(self)

def parse_json_loose(text: str) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1]
    if starts:
        cleaned = cleaned[min(starts): max(cleaned.rfind("}"), cleaned.rfind("]")) + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", cleaned))


# A validator returns the (possibly normalised) object and a list of problems to send back to the model.
Validator = Callable[[Any], tuple[Any, list[str]]]


class LLM:
    def __init__(self, settings: Settings | None = None, cache_dir: Path | None = None, *,
                 budget: float | None = None, label: str = ""):
        self.settings = settings or get_settings()
        self.cache_dir = cache_dir
        self.sem = asyncio.Semaphore(self.settings.concurrency)
        headers = {"Authorization": f"Bearer {self.settings.api_key}", "User-Agent": "chinese-novel-to-game/0.1",
                   **self.settings.headers}
        if self.settings.session_header:
            # gateways route and cache by session; one id per run keeps a run's shared prompts together
            headers[self.settings.session_header] = uuid.uuid4().hex
        self.client = httpx.AsyncClient(
            base_url=self.settings.base_url,
            headers=headers,
            timeout=httpx.Timeout(self.settings.read_timeout, connect=20),
        )
        self.usage: dict[str, Usage] = defaultdict(Usage)
        self.budget = self.settings.budget if budget is None else budget
        self.label = label
        self.started = time.monotonic()
        self.inflight = 0
        self.retries = 0
        self.timeouts = 0
        self.job_label, self.job_total, self.job_done = "", 0, 0
        self._last_report = 0.0
        self._heartbeat: asyncio.Task | None = None

    async def aclose(self) -> None:
        if self._heartbeat:
            self._heartbeat.cancel()
        self._report(final=True)
        self._log_spend()
        await self.client.aclose()

    async def __aenter__(self) -> "LLM":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # ---- spending and progress -------------------------------------------------------------
    def spent(self) -> float:
        return sum(u.cost(self.settings.price) for u in self.usage.values())

    def _check_budget(self, reserve: bool = False) -> None:
        """Refuse a new paid call once spending, plus the expected cost of calls still in flight when
        `reserve`, reaches the cap."""
        if not self.budget:
            return
        spent = self.spent()
        calls = sum(u.calls for u in self.usage.values())
        pending = self.inflight * spent / calls if reserve and calls else 0.0
        if spent + pending >= self.budget:
            raise BudgetExceeded(f"budget ${self.budget:.2f} reached (spent ${spent:.3f}, ~${pending:.3f} in flight); "
                                 "finished work is saved, rerun with a higher --budget to continue")

    def job(self, label: str, total: int) -> None:
        """Name the unit of work so progress lines read e.g. `cards 12/80`."""
        self.job_label, self.job_total, self.job_done = label, total, 0

    def tick(self, n: int = 1) -> None:
        self.job_done += n

    def _status(self) -> str:
        calls = sum(u.calls for u in self.usage.values())
        cached = sum(u.cached_calls for u in self.usage.values())
        mins = (time.monotonic() - self.started) / 60
        job = f"{self.job_label} {self.job_done}/{self.job_total} · " if self.job_total else ""
        cap = f"/{self.budget:.2f}" if self.budget else ""
        return (f"[llm] {job}calls {calls} cached {cached} inflight {self.inflight} retries {self.retries} "
                f"timeouts {self.timeouts} · ${self.spent():.3f}{cap} · {mins:.1f} min")

    def _report(self, final: bool = False) -> None:
        if final and not any(u.calls or u.cached_calls for u in self.usage.values()):
            return
        print(self._status() + (" · done" if final else ""), file=sys.stderr, flush=True)
        self._last_report = time.monotonic()

    async def _beat(self) -> None:
        while True:
            await asyncio.sleep(self.settings.progress_every)
            if self.inflight:
                self._report()

    def _log_spend(self) -> None:
        if not self.cache_dir or not any(u.calls for u in self.usage.values()):
            return
        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "label": self.label or " ".join(sys.argv[1:4]),
               "cost": round(self.spent(), 5), "minutes": round((time.monotonic() - self.started) / 60, 2),
               "usage": self.usage_summary()}
        with (self.cache_dir.parent / "spend.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _deadline(self, max_tokens: int) -> float:
        # one wall-clock limit per request: a socket that went quiet (e.g. after the laptop slept)
        # can otherwise wait forever, since httpx only times individual reads
        return self.settings.request_timeout or 120 + max_tokens / 50

    def _cache_path(self, key: str) -> Path | None:
        return self.cache_dir / key[:2] / f"{key}.json" if self.cache_dir else None
    async def chat(self, role: str, messages: list[dict], *, max_tokens: int = 8192,
                   temperature: float = 0.2, json_mode: bool = False, use_cache: bool = True) -> str:
        body: dict[str, Any] = {
            "model": self.settings.models[role],
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            **self.settings.extra_body,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        key = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        path = self._cache_path(key)
        if use_cache and path and path.exists():
            self.usage[role].cached_calls += 1
            return json.loads(path.read_text(encoding="utf-8"))["content"]

        last_err: Exception | None = None
        if self._heartbeat is None and self.settings.progress_every > 0:
            self._heartbeat = asyncio.create_task(self._beat())
        for attempt in range(6):
            self._check_budget()
            t0 = time.monotonic()
            try:
                async with self.sem:
                    # calls queued on the semaphore all passed the check above before any was paid for
                    self._check_budget(reserve=True)
                    self.inflight += 1
                    try:
                        resp = await asyncio.wait_for(self.client.post("/chat/completions", json=body),
                                                      self._deadline(max_tokens))
                    finally:
                        self.inflight -= 1
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                if resp.status_code >= 400:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:500]}")  # not retryable
                data = resp.json()
                choice = data["choices"][0]
                content = choice["message"].get("content") or ""
                self.usage[role].add(data.get("usage", {}), time.monotonic() - t0)
                if choice.get("finish_reason") == "length":
                    raise LLMError("output truncated (finish_reason=length); raise max_tokens or shrink input")
                if path:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({"content": content, "usage": data.get("usage")}, ensure_ascii=False), encoding="utf-8")
                return content
            except BudgetExceeded:
                raise
            except (httpx.HTTPError, LLMError, KeyError, json.JSONDecodeError, asyncio.TimeoutError) as e:
                last_err = e
                if isinstance(e, LLMError) and ("HTTP 4" in str(e) and "HTTP 429" not in str(e) or "truncated" in str(e)):
                    raise
                if isinstance(e, asyncio.TimeoutError):
                    self.timeouts += 1
                    last_err = LLMError(f"no answer within {self._deadline(max_tokens):.0f}s")
                self.retries += 1
                await asyncio.sleep(min(60, 2 ** attempt + random.random()))
        raise LLMError(f"giving up after retries: {last_err}")

    async def chat_json(self, role: str, messages: list[dict], *, validate: Validator | None = None,
                        max_attempts: int = 3, history: list[list[str]] | None = None, **kw) -> tuple[Any, list[str]]:
        """Return (object, remaining_problems). Problems found by `validate` are fed back to the model;
        `history`, if given, receives the problem list of every attempt."""
        msgs = list(messages)
        obj: Any = None
        problems: list[str] = []
        for attempt in range(max_attempts):
            text = await self.chat(role, msgs, json_mode=True, **kw)
            try:
                obj = parse_json_loose(text)
            except json.JSONDecodeError as e:
                problems = [f"输出不是合法 JSON：{e}"]
                if history is not None:
                    history.append(problems)
            else:
                if validate is None:
                    return obj, []
                obj, problems = validate(obj)
                if history is not None:
                    history.append(problems)
                if not problems:
                    return obj, []
            if attempt + 1 < max_attempts:
                feedback = "上一次输出有以下问题，请修正后重新输出完整 JSON：\n" + "\n".join(f"- {p}" for p in problems[:40])
                msgs = msgs + [{"role": "assistant", "content": text}, {"role": "user", "content": feedback}]
        return obj, problems

    def usage_summary(self) -> dict:
        return {role: u.to_dict() for role, u in self.usage.items()}
