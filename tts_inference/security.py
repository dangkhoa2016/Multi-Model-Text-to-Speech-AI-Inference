"""CPU-only auth, pre-auth ceilings, failed-key lockout and Redis-wide quotas.

Do not trust client-supplied X-Forwarded-For. ASGI server/proxy must establish
scope['client'] correctly. No raw tokens, input or audio are written to logs.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Protocol

from starlette.datastructures import Headers
from starlette.responses import JSONResponse


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after: int = 0


@dataclass(frozen=True)
class RatePolicy:
    """Server-side Redis quotas. These are per deployment, not client-supplied."""
    preauth_peer_per_minute: int = 120
    preauth_global_per_minute: int = 600
    auth_failure_limit: int = 3
    auth_failure_window_seconds: int = 300
    auth_lockout_seconds: int = 900
    jobs_per_window: int = 12
    job_window_seconds: int = 300
    jobs_per_client_day: int = 100
    jobs_global_day: int = 500
    polls_per_minute: int = 120

    @classmethod
    def from_env(cls) -> RatePolicy:
        names = {
            "preauth_peer_per_minute": "TEXT_TO_SPEECH_RATE_PREAUTH_REQUESTS_PER_PEER_PER_MINUTE",
            "preauth_global_per_minute": "TEXT_TO_SPEECH_RATE_PREAUTH_REQUESTS_GLOBAL_PER_MINUTE",
            "auth_failure_limit": "TEXT_TO_SPEECH_AUTHENTICATION_FAILURE_LIMIT",
            "auth_failure_window_seconds": "TEXT_TO_SPEECH_AUTHENTICATION_FAILURE_WINDOW_SECONDS",
            "auth_lockout_seconds": "TEXT_TO_SPEECH_AUTHENTICATION_LOCKOUT_SECONDS",
            "jobs_per_window": "TEXT_TO_SPEECH_RATE_MAX_JOBS_PER_WINDOW",
            "job_window_seconds": "TEXT_TO_SPEECH_RATE_JOBS_WINDOW_SECONDS",
            "jobs_per_client_day": "TEXT_TO_SPEECH_RATE_MAX_JOBS_PER_CLIENT_PER_DAY",
            "jobs_global_day": "TEXT_TO_SPEECH_RATE_MAX_JOBS_GLOBAL_PER_DAY",
            "polls_per_minute": "TEXT_TO_SPEECH_RATE_MAX_POLLS_PER_MINUTE",
        }
        defaults = cls()
        params = {}
        for field, env in names.items():
            raw = os.getenv(env)
            value = int(raw) if raw is not None else getattr(defaults, field)
            if not 1 <= value <= 1_000_000:
                raise ValueError(f"{env} must be between 1 and 1000000")
            params[field] = value
        return cls(**params)


class RateGuard(Protocol):
    async def preauth(self, peer: str) -> RateDecision: ...
    async def failure(self, peer: str) -> RateDecision: ...
    async def success(self, peer: str) -> None: ...
    async def allow_job(self, owner: str) -> RateDecision: ...
    async def allow_poll(self, owner: str) -> RateDecision: ...


class InProcessGuard:
    """Useful in an offline mock only; NOT distributed or deployment-safe."""
    def __init__(self, job_limit: int = 12, job_window: int = 300):
        self.lock = threading.Lock()
        self.counters = defaultdict(deque)
        self.job_limit = job_limit
        self.job_window = job_window
        self.auth_lockouts: dict[str, float] = {}

    def _hit(self, key: tuple, limit: int, window: int) -> RateDecision:
        now = time.monotonic()
        with self.lock:
            events = self.counters[key]
            while events and events[0] <= now - window:
                events.popleft()
            if len(events) >= limit:
                return RateDecision(False, max(1, int(window - (now - events[0]) + 0.999)))
            events.append(now)
            return RateDecision(True)

    async def preauth(self, peer: str) -> RateDecision:
        # Per-peer and process-wide ceilings apply before comparing credentials.
        r = self._hit(("peer", peer), 120, 60)
        if not r.allowed:
            return r
        return self._hit(("global",), 600, 60)

    async def failure(self, peer: str) -> RateDecision:
        now = time.monotonic()
        with self.lock:
            locked_until = self.auth_lockouts.get(peer, 0)
            if locked_until > now:
                return RateDecision(False, max(1, int(locked_until - now + 0.999)))
            events = self.counters[("auth_fail", peer)]
            while events and events[0] <= now - 300:
                events.popleft()
            events.append(now)
            if len(events) >= 3:
                self.auth_lockouts[peer] = now + 900
                return RateDecision(False, 900)
            return RateDecision(True)

    async def success(self, peer: str) -> None:
        with self.lock:
            self.counters.pop(("auth_fail", peer), None)
            self.auth_lockouts.pop(peer, None)

    async def allow_job(self, owner: str) -> RateDecision:
        return self._hit(("jobs", owner), self.job_limit, self.job_window)

    async def allow_poll(self, owner: str) -> RateDecision:
        return self._hit(("polls", owner), 120, 60)


# Atomic cross-replica rate limiting. Uses Redis server time for synchronized windows.
# All keys are fully namespaced and all decisions are made in one Lua operation.
_RATE_SCRIPT = """
local now = tonumber(redis.call('TIME')[1])
local n = #KEYS
local keys = {}
local wait = 0
for i = 1, n do
  local lim = tonumber(ARGV[2*i-1])
  local window = tonumber(ARGV[2*i])
  keys[i] = KEYS[i] .. ':' .. math.floor(now/window)
  local count = tonumber(redis.call('GET', keys[i]) or '0')
  if count >= lim then
    local rem = window - (now % window)
    if rem > wait then wait = rem end
  end
end
if wait > 0 then return {0, wait} end
for i = 1, n do
  redis.call('INCR', keys[i])
  redis.call('EXPIRE', keys[i], tonumber(ARGV[2*i])*2)
end
return {1, 0}
"""

_FAIL_SCRIPT = """
local now = tonumber(redis.call('TIME')[1])
local bucket = KEYS[1] .. ':' .. math.floor(now / tonumber(ARGV[1]))
local lock = KEYS[1] .. ':locked'
local ttl = redis.call('TTL', lock)
if ttl > 0 then return {0, ttl} end
local count = redis.call('INCR', bucket)
if count == 1 then redis.call('EXPIRE', bucket, tonumber(ARGV[1])*2) end
if count >= tonumber(ARGV[2]) then
  redis.call('SET', lock, '1', 'EX', tonumber(ARGV[3]))
  return {0, tonumber(ARGV[3])}
end
return {1, 0}
"""

_CLEAR_SCRIPT = """
local now = tonumber(redis.call('TIME')[1])
local suffix = math.floor(now / tonumber(ARGV[1]))
redis.call('DEL', KEYS[1] .. ':locked', KEYS[1] .. ':' .. suffix)
return 1
"""


class RedisGuard:
    """Fail-closed distributed limits; construct only with trusted Redis TLS URL."""
    def __init__(self, redis_client, *, namespace: str = "tts-v1", policy: RatePolicy | None = None):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", namespace):
            raise ValueError("Invalid Redis namespace")
        self.redis = redis_client
        self.ns = namespace
        self.policy = policy or RatePolicy()

    def _key(self, category: str, identity: str) -> str:
        # Do not store a client identity or network address directly in Redis keys.
        # Hash-tag keeps every key in the same Redis Cluster slot, required
        # for atomic multi-key Lua checks on clustered Redis/Valkey.
        return f"{{{self.ns}}}:{category}:{hashlib.sha256(identity.encode()).hexdigest()[:32]}"

    async def _hit(self, categories: list[tuple[str, str, int, int]]) -> RateDecision:
        keys = [self._key(cat, owner) for cat, owner, _, _ in categories]
        params = [value for _, _, limit, window in categories for value in (limit, window)]
        ok, retry = await self.redis.eval(_RATE_SCRIPT, len(keys), *keys, *params)
        return RateDecision(bool(ok), int(retry))

    async def preauth(self, peer: str) -> RateDecision:
        p = self.policy
        return await self._hit([
            ("pre-peer", peer, p.preauth_peer_per_minute, 60),
            ("pre-global", "global", p.preauth_global_per_minute, 60),
        ])

    async def failure(self, peer: str) -> RateDecision:
        p = self.policy
        ok, retry = await self.redis.eval(_FAIL_SCRIPT, 1, self._key("auth-fail", peer),
                                          p.auth_failure_window_seconds, p.auth_failure_limit,
                                          p.auth_lockout_seconds)
        return RateDecision(bool(ok), int(retry))

    async def success(self, peer: str) -> None:
        await self.redis.eval(_CLEAR_SCRIPT, 1, self._key("auth-fail", peer),
                              self.policy.auth_failure_window_seconds)

    async def allow_job(self, owner: str) -> RateDecision:
        p = self.policy
        return await self._hit([
            ("jobs", owner, p.jobs_per_window, p.job_window_seconds),
            ("jobs-daily", owner, p.jobs_per_client_day, 86400),
            ("jobs-global-daily", "global", p.jobs_global_day, 86400),
        ])

    async def allow_poll(self, owner: str) -> RateDecision:
        return await self._hit([("polls", owner, self.policy.polls_per_minute, 60)])


def load_registry(client_keys: dict[str, str] | None = None, *,
                  production: bool = False) -> tuple[dict[str, str], str | None]:
    """Store SHA-256 of high-entropy API tokens; never compare Python plaintext maps."""
    if client_keys is not None:
        if production:
            raise ValueError("Plaintext client keys cannot be injected in production")
        hashes = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in client_keys.items()}
    else:
        raw = os.getenv("TEXT_TO_SPEECH_CLIENT_KEY_HASHES_JSON")
        if raw:
            hashes = json.loads(raw)
        elif not production and os.getenv("TEXT_TO_SPEECH_DEVELOPMENT_CLIENT_KEYS_JSON"):
            keys = json.loads(os.environ["TEXT_TO_SPEECH_DEVELOPMENT_CLIENT_KEYS_JSON"])
            hashes = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in keys.items()}
        else:
            raise RuntimeError("TEXT_TO_SPEECH_CLIENT_KEY_HASHES_JSON is required")
    if not isinstance(hashes, dict) or not hashes or any(
        not isinstance(k, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", k)
        or not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
        for k, v in hashes.items()
    ):
        raise RuntimeError("Client registry must map client IDs to 64-digit lowercase SHA256 hashes")
    revoked = {x for x in os.getenv("TEXT_TO_SPEECH_REVOKED_CLIENT_IDS", "").split(",") if x}
    hashes = {k: v for k, v in hashes.items() if k not in revoked}
    if not hashes:
        raise RuntimeError("No active client credentials configured")
    owner_hash = os.getenv("TEXT_TO_SPEECH_OWNER_TOKEN_SHA256")
    if owner_hash and not re.fullmatch(r"[0-9a-f]{64}", owner_hash):
        raise RuntimeError("TEXT_TO_SPEECH_OWNER_TOKEN_SHA256 must be a SHA256 hex digest")
    if production and not owner_hash:
        raise RuntimeError("Owner key hash required in production")
    return hashes, owner_hash


class SecurityMiddleware:
    """Authenticate before accepting request body; cap streaming body bytes."""
    def __init__(self, app, guard: RateGuard, hashes: dict[str, str], owner_hash: str | None,
                 body_limit: int = 8_500_000):
        self.app = app
        self.guard = guard
        self.hashes = hashes
        self.owner_hash = owner_hash
        self.body_limit = body_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path == "/v1/health":
            return await self.app(scope, receive, send)
        if not path.startswith("/v1/"):
            return await self.app(scope, receive, send)

        async def rejected(code: int, detail: str, retry: int = 0):
            headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
            if retry:
                headers["Retry-After"] = str(retry)
            if code == 401:
                headers["WWW-Authenticate"] = "Bearer"
            return await JSONResponse({"detail": detail}, status_code=code, headers=headers)(scope, receive, send)

        peer = (scope.get("client") or ("unknown", 0))[0]
        try:
            decision = await self.guard.preauth(peer)
        except Exception:  # noqa: BLE001 - fail closed for any Redis/guard failure
            return await rejected(503, "Rate-limit service unavailable")
        if not decision.allowed:
            return await rejected(429, "Request limit exceeded", decision.retry_after)
        # Reject duplicate Authorization headers to avoid proxy/parser ambiguity.
        raw_auth = [value for key, value in scope.get("headers", [])
                    if key.lower() == b"authorization"]
        header = raw_auth[0].decode("ascii", errors="ignore") if len(raw_auth) == 1 else ""
        token = header[7:] if header[:7].lower() == "bearer " else ""
        if not 24 <= len(token) <= 4096 or not token.isascii():
            token = ""  # Never parse invalid long credentials.
        digest = hashlib.sha256(token.encode()).hexdigest()
        # Deliberately compare every configured credential without short-circuiting.
        matches = [cid for cid, val in self.hashes.items() if hmac.compare_digest(digest, val)]
        is_owner = bool(self.owner_hash and hmac.compare_digest(digest, self.owner_hash))
        if not matches and not is_owner:
            try:
                result = await self.guard.failure(peer)
            except Exception:  # noqa: BLE001 - fail closed for authentication failures
                return await rejected(503, "Authentication service unavailable")
            if not result.allowed:
                return await rejected(429, "Too many authentication failures", result.retry_after)
            return await rejected(401, "Invalid Bearer token")
        try:
            await self.guard.success(peer)
        except Exception:  # noqa: BLE001 - fail closed for authentication failures
            return await rejected(503, "Authentication service unavailable")
        if path.startswith("/v1/ops/") and not is_owner:
            return await rejected(404, "Not found")
        state = scope.setdefault("state", {})
        state["owner"] = "__operator__" if is_owner else matches[0]
        state["is_operator"] = is_owner

        # Oversized requests are rejected after auth but before FastAPI JSON/Pydantic parsing.
        length = Headers(scope=scope).get("content-length")
        if length:
            try:
                if int(length) > self.body_limit:
                    return await rejected(413, "Request body too large")
            except ValueError:
                return await rejected(400, "Invalid Content-Length")
        if scope.get("method") in ("POST", "PUT", "PATCH"):
            body = bytearray()
            while True:
                try:
                    message = await receive()
                except Exception:  # noqa: BLE001 - reject malformed request streams
                    return await rejected(400, "Invalid request body")
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > self.body_limit:
                    return await rejected(413, "Request body too large")
                if not message.get("more_body", False):
                    break
            sent_body = False
            async def replay():
                nonlocal sent_body
                if not sent_body:
                    sent_body = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return {"type": "http.disconnect"}
            receive = replay

        async def secured_send(message):
            if message["type"] == "http.response.start":
                existing = list(message.get("headers", []))
                if not any(k.lower() == b"cache-control" for k, _ in existing):
                    existing.append((b"cache-control", b"no-store"))
                if not any(k.lower() == b"x-content-type-options" for k, _ in existing):
                    existing.append((b"x-content-type-options", b"nosniff"))
                message["headers"] = existing
            await send(message)
        await self.app(scope, receive, secured_send)
