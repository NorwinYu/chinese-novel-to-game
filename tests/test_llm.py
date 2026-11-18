"""LLM client safety limits, checked against a fake gateway (no network, no spend)."""
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from n2g.config import Settings  # noqa: E402
from n2g.llm import LLM, BudgetExceeded, LLMError  # noqa: E402


def settings(**kw):
    base = dict(base_url="http://fake", api_key="x", models={"extract": "m"}, concurrency=2,
                progress_every=0.05, request_timeout=0.3)
    base.update(kw)
    return Settings(**base)


def answer(cost=0.01):
    return httpx.Response(200, json={"choices": [{"message": {"content": "{\"ok\": 1}"}, "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": 10, "completion_tokens": 5, "gateway_cost": {"list_cost": cost}}})


def client(llm, handler):
    llm.client = httpx.AsyncClient(base_url="http://fake", transport=httpx.MockTransport(handler))
    return llm


def test_budget_stops_new_calls(tmp_path):
    async def run():
        llm = client(LLM(settings(), cache_dir=tmp_path / "cache", budget=0.025), lambda r: answer(0.01))
        done = 0
        try:
            for i in range(10):
                await llm.chat("extract", [{"role": "user", "content": str(i)}])
                done += 1
        except BudgetExceeded:
            pass
        await llm.aclose()
        return done
    assert asyncio.run(run()) == 3
    log = [json.loads(x) for x in (tmp_path / "spend.jsonl").read_text().splitlines()]
    assert abs(log[-1]["cost"] - 0.03) < 1e-9


def test_cached_calls_are_free_under_budget(tmp_path):
    async def run():
        llm = client(LLM(settings(), cache_dir=tmp_path / "cache", budget=0.005), lambda r: answer(0.01))
        await llm.chat("extract", [{"role": "user", "content": "a"}])
        for _ in range(3):  # over budget now, but the answer is cached
            await llm.chat("extract", [{"role": "user", "content": "a"}])
        try:
            await llm.chat("extract", [{"role": "user", "content": "b"}])
            raise AssertionError("expected BudgetExceeded")
        except BudgetExceeded:
            pass
        await llm.aclose()
    asyncio.run(run())


def test_stalled_request_times_out_and_retries(tmp_path, monkeypatch, capsys):
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.Event().wait()  # a socket that never answers
        return answer()

    async def run():
        llm = client(LLM(settings(), cache_dir=None), handler)
        real_sleep = asyncio.sleep
        monkeypatch.setattr("n2g.llm.asyncio.sleep", lambda s: real_sleep(min(s, 0.01)))
        out = await llm.chat("extract", [{"role": "user", "content": "x"}])
        t = llm.timeouts
        await llm.aclose()
        return out, t
    out, timeouts = asyncio.run(run())
    assert out == "{\"ok\": 1}" and timeouts == 1 and calls["n"] == 2
    err = capsys.readouterr().err
    assert "inflight 1" in err  # the heartbeat reported while the request hung


def test_client_errors_are_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, text="bad key")

    async def run():
        llm = client(LLM(settings(), cache_dir=None), handler)
        try:
            await llm.chat("extract", [{"role": "user", "content": "x"}])
        except LLMError as e:
            assert "401" in str(e)
        await llm.aclose()
    asyncio.run(run())
    assert calls["n"] == 1


def test_budget_holds_under_concurrency(tmp_path):
    posted = {"n": 0}

    async def handler(request):
        posted["n"] += 1
        await asyncio.sleep(0.01)
        return answer(0.01)

    async def run():
        llm = client(LLM(settings(concurrency=4), cache_dir=tmp_path / "cache", budget=0.025), handler)
        res = await asyncio.gather(*(llm.chat("extract", [{"role": "user", "content": str(i)}]) for i in range(40)),
                                   return_exceptions=True)
        await llm.aclose()
        return res
    res = asyncio.run(run())
    assert sum(isinstance(r, BudgetExceeded) for r in res) == len(res) - posted["n"]
    assert posted["n"] <= 3 + 4  # at most one more batch than the cap allows, not all queued calls
