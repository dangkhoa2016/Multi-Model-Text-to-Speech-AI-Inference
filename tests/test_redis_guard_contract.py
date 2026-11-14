"""Offline Redis adapter protocol tests; does NOT execute Lua on a live server."""
import asyncio

from tts_inference.security import _CLEAR_SCRIPT, _FAIL_SCRIPT, _RATE_SCRIPT, RedisGuard


class InMemoryScriptSimulator:
    """Minimal in-process Lua intent simulation to test wiring/ownership only."""

    def __init__(self):
        self.now = 1_760_000_000
        self.counters = {}
        self.lockouts = {}
        self.calls = 0

    async def eval(self, script, count, *values):
        self.calls += 1
        keys = list(values[:count])
        args = list(values[count:])
        assert all(k.startswith("{tts-v1}:") for k in keys)
        if script == _RATE_SCRIPT:
            chosen = [(f"{k}:{self.now // int(args[2*i+1])}", int(args[2*i]), int(args[2*i+1]))
                      for i, k in enumerate(keys)]
            waits = [(window - self.now % window) for key, limit, window in chosen
                     if self.counters.get(key, 0) >= limit]
            if waits:
                return [0, max(waits)]
            for key, _, _ in chosen:
                self.counters[key] = self.counters.get(key, 0) + 1
            return [1, 0]
        if script == _FAIL_SCRIPT:
            window, max_failures, lock_sec = map(int, args)
            key = keys[0]
            if self.lockouts.get(key, 0) > self.now:
                return [0, self.lockouts[key] - self.now]
            bucket = f"{key}:{self.now // window}"
            self.counters[bucket] = self.counters.get(bucket, 0) + 1
            if self.counters[bucket] >= max_failures:
                self.lockouts[key] = self.now + lock_sec
                return [0, lock_sec]
            return [1, 0]
        if script == _CLEAR_SCRIPT:
            key = keys[0]
            self.lockouts.pop(key, None)
            self.counters.pop(f"{key}:{self.now // int(args[0])}", None)
            return 1
        raise AssertionError("Unexpected Lua script")


def test_shared_rate_budget_across_instances():
    async def run():
        redis = InMemoryScriptSimulator()
        a, b = RedisGuard(redis), RedisGuard(redis)
        for i in range(12):
            assert (await (a if i % 2 else b).allow_job("alice")).allowed
        limit = await a.allow_job("alice")
        assert not limit.allowed and limit.retry_after > 0
        assert (await b.allow_job("bob")).allowed
        assert redis.calls == 14
        assert all("alice" not in key for key in redis.counters)
        # Redis Cluster hash-tags force every key into the SAME cluster slot.
        assert a._key("pre-peer", "1.2.3.4").startswith("{tts-v1}:")
    asyncio.run(run())


def test_redis_failed_auth_lockout_and_clear():
    async def run():
        redis = InMemoryScriptSimulator()
        a, b = RedisGuard(redis), RedisGuard(redis)
        assert (await a.failure("peer")).allowed
        assert (await b.failure("peer")).allowed
        locked = await a.failure("peer")
        assert not locked.allowed and locked.retry_after == 900
        assert not (await b.failure("peer")).allowed
        await b.success("peer")
        assert (await a.failure("peer")).allowed
    asyncio.run(run())


def test_preauth_global_budget_and_poll_limit():
    async def run():
        redis = InMemoryScriptSimulator()
        a = RedisGuard(redis)
        for i in range(120):
            assert (await a.preauth("peer-A")).allowed
        assert not (await a.preauth("peer-A")).allowed
        assert (await a.preauth("peer-B")).allowed
        for i in range(120):
            assert (await a.allow_poll("alice")).allowed
        assert not (await a.allow_poll("alice")).allowed
    asyncio.run(run())


def test_global_daily_limit_and_configurable_quotas():
    from tts_inference.security import RatePolicy
    async def run():
        redis = InMemoryScriptSimulator()
        a = RedisGuard(redis, policy=RatePolicy(
            jobs_per_window=100, jobs_per_client_day=10, jobs_global_day=2))
        assert (await a.allow_job("alice")).allowed
        assert (await a.allow_job("bob")).allowed
        assert not (await a.allow_job("alice")).allowed  # Shared global daily cap.
    asyncio.run(run())


def test_rate_policy_env_validation(monkeypatch):
    import pytest

    from tts_inference.security import RatePolicy
    monkeypatch.setenv("TEXT_TO_SPEECH_RATE_MAX_JOBS_PER_WINDOW", "8")
    assert RatePolicy.from_env().jobs_per_window == 8
    monkeypatch.setenv("TEXT_TO_SPEECH_RATE_MAX_JOBS_PER_WINDOW", "-1")
    with pytest.raises(ValueError):
        RatePolicy.from_env()
