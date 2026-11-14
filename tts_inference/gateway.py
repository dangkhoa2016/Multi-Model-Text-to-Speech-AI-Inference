"""Authenticated CPU-only HTTP gateway; injectable durable store or offline mock."""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .contracts import ModelID, SpeechRequest, model_manifest, prepare_request
from .persistence import IdempotencyConflict, JobStore, QueueFull
from .security import InProcessGuard, RateDecision, RateGuard, SecurityMiddleware, load_registry

LOG = logging.getLogger(__name__)


class Dispatcher(Protocol):
    async def submit(self, job: dict, owner: str) -> str: ...
    async def inspect(self, job_id: str, owner: str) -> dict | None: ...
    async def fetch_audio(self, job_id: str, owner: str) -> bytes | None: ...


async def _approved(rate_call: Awaitable[RateDecision], detail: str) -> None:
    try:
        decision = await rate_call
    except Exception as exc:
        raise HTTPException(503, "Rate-limit service unavailable") from exc
    if not decision.allowed:
        raise HTTPException(429, detail, headers={"Retry-After": str(max(1, decision.retry_after))})


def create_gateway(
    dispatcher: Dispatcher | None = None, *, client_keys: dict[str, str] | None = None,
    job_store: JobStore | None = None, guard: RateGuard | None = None,
    wake_job: Callable[[str], Awaitable[None]] | None = None,
    production: bool = False, enabled_models: set[ModelID] | None = None,
) -> FastAPI:
    """Production requires durable PostgreSQL, Redis and CPU-only Modal wake callback."""
    if production and (job_store is None or guard is None or wake_job is None):
        raise RuntimeError("Production requires a durable JobStore, shared rate guard and CPU dispatcher")
    if job_store is None and dispatcher is None:
        raise ValueError("A durable store or offline dispatcher is required")
    hashes, owner_hash = load_registry(client_keys, production=production)
    limiter = guard or InProcessGuard()
    available_models = set(ModelID) if enabled_models is None else set(enabled_models)
    if not available_models <= set(ModelID):
        raise ValueError("Unknown enabled model")
    api = FastAPI(title="Text-to-Speech AI Inference", version="1.0.0",
                  docs_url=None, redoc_url=None, openapi_url=None)
    api.add_middleware(SecurityMiddleware, guard=limiter, hashes=hashes, owner_hash=owner_hash)

    @api.exception_handler(RequestValidationError)
    async def redact_validation(_request: Request, exc: RequestValidationError):
        # Pydantic's default errors may include base64 reference audio and text.
        errors = [{"loc": [str(v) for v in err.get("loc", ())],
                   "type": err.get("type", "invalid"), "msg": err.get("msg", "Invalid input")}
                  for err in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422, headers={"Cache-Control": "no-store"})

    def principal(request: Request) -> str:
        return request.state.owner

    @api.get("/v1/health")
    async def health():
        return {"status": "ok", "service": "text-to-speech-api", "version": "1.0.0"}

    @api.get("/v1/models")
    async def models(_owner: str = Depends(principal)):
        return {"data": [{**model_manifest(model), "enabled": model in available_models} for model in ModelID]}

    @api.get("/v1/models/{model_id}")
    async def model_info(model_id: ModelID, _owner: str = Depends(principal)):
        return {**model_manifest(model_id), "enabled": model_id in available_models}

    @api.post("/v1/audio/jobs", status_code=status.HTTP_202_ACCEPTED)
    async def create_job(
        request: SpeechRequest, response: Response, owner: str = Depends(principal),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        if request.model not in available_models:
            raise HTTPException(503, "Model disabled by deployment operator")
        if idempotency_key and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", idempotency_key):
            raise HTTPException(422, "Invalid Idempotency-Key (8–128 ASCII characters)")
        try:
            prepared = prepare_request(request)
        except ValidationError as exc:
            raise HTTPException(422, [{"loc": [str(v) for v in err.get("loc", ())],
                                       "type": err.get("type", "invalid"),
                                       "msg": err.get("msg", "Invalid input")} for err in exc.errors()]) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

        if job_store:
            existing = None
            try:
                if idempotency_key:
                    existing = await asyncio.to_thread(job_store.lookup_idempotency, owner, idempotency_key, prepared)
                if existing:
                    job_id, created = existing, False
                else:
                    await _approved(limiter.allow_job(owner), "Job rate limit exceeded")
                    job_id, created = await asyncio.to_thread(job_store.enqueue, owner, prepared, idempotency_key)
            except IdempotencyConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            except QueueFull as exc:
                raise HTTPException(429, str(exc), headers={"Retry-After": "30"}) from exc
            except HTTPException:
                raise
            # Treat unexpected persistence errors as unavailable, without exposing secrets.
            except Exception as exc:
                LOG.error("Durable enqueue unavailable")
                raise HTTPException(503, "Queue service unavailable") from exc
            if created and wake_job is not None:
                try:
                    await wake_job(job_id)  # Only spawns CPU coordinator, never GPU here.
                except Exception:  # noqa: BLE001 - any wake failure must not requeue GPU work
                    # Job is already durably QUEUED; the periodic CPU sweep will wake it.
                    LOG.warning("CPU coordinator wake failed; job remains durably queued")
        else:
            await _approved(limiter.allow_job(owner), "Job rate limit exceeded")
            try:
                job_id = await dispatcher.submit(prepared, owner)
            except Exception as exc:
                LOG.error("Mock/legacy job submission failure")
                raise HTTPException(503, "Inference dispatch unavailable") from exc
            created = True

        response.headers["Location"] = f"/v1/audio/jobs/{job_id}"
        return {"job_id": job_id, "model": request.model.value,
                "status": "queued" if job_store else "submitted", "reused": not created,
                "status_url": f"/v1/audio/jobs/{job_id}"}

    @api.get("/v1/audio/jobs/{job_id}")
    async def get_job(job_id: str, owner: str = Depends(principal)):
        await _approved(limiter.allow_poll(owner), "Job polling rate limit exceeded")
        try:
            info = (await asyncio.to_thread(job_store.inspect, job_id, owner)) if job_store else await dispatcher.inspect(job_id, owner)
        except Exception as exc:
            LOG.error("Job lookup temporarily unavailable")
            raise HTTPException(503, "Job status temporarily unavailable") from exc
        if info is None:
            raise HTTPException(404, "Job not found")
        return info

    @api.get("/v1/audio/jobs/{job_id}/audio")
    async def get_audio(job_id: str, owner: str = Depends(principal)):
        await _approved(limiter.allow_poll(owner), "Audio retrieval rate limit exceeded")
        try:
            audio = (await asyncio.to_thread(job_store.audio, job_id, owner)) if job_store else await dispatcher.fetch_audio(job_id, owner)
        except TimeoutError as exc:
            raise HTTPException(409, "Audio not ready") from exc
        except Exception as exc:
            LOG.error("Audio retrieval temporarily unavailable")
            raise HTTPException(503, "Audio temporarily unavailable") from exc
        if audio is None:
            raise HTTPException(404, "Audio not found")
        return Response(audio, media_type="audio/wav", headers={
            "Cache-Control": "private, no-store", "Content-Disposition": f'attachment; filename="{job_id}.wav"',
        })

    @api.get("/v1/ops/metrics")
    async def metrics(owner: str = Depends(principal)):
        if job_store is None:
            return {"scope": "local_mock", "job_counts_by_state": {}}
        try:
            return await asyncio.to_thread(job_store.safe_metrics)
        except Exception as exc:
            raise HTTPException(503, "Metrics temporarily unavailable") from exc

    return api
