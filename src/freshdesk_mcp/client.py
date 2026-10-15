"""Async Freshdesk REST client: Basic auth (API key), rate limiting, safe retries."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import Config
from .ratelimit import RateLimitedError, RateLimiter, backoff_seconds


class FreshdeskError(Exception):
    def __init__(self, status: int, message: str, details: Any = None):
        self.status = status
        self.details = details
        super().__init__(f"Freshdesk API error {status}: {message}")


class FreshdeskClient:
    def __init__(self, config: Config, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.limiter = RateLimiter(config.rate_limit_per_min, config.max_concurrency)
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            auth=(config.api_key, "X"),  # Freshdesk: API key as username, any password
            timeout=httpx.Timeout(20.0),
            headers={"Content-Type": "application/json"},
            transport=transport,
        )
        self.retries_performed = 0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        method = method.upper()
        idempotent = method in {"GET", "HEAD", "PUT"}
        last_retry_after = 0.0
        for attempt in range(self.config.max_retries + 1):
            await self.limiter.acquire()
            async with self.limiter.semaphore:
                try:
                    resp = await self._http.request(method, path, params=params, json=json)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    # Never auto-retry non-idempotent POSTs: the request may have succeeded.
                    if method == "GET" and attempt < self.config.max_retries:
                        self.retries_performed += 1
                        await asyncio.sleep(backoff_seconds(attempt, None))
                        continue
                    raise FreshdeskError(0, f"network error: {type(exc).__name__}") from exc

            self.limiter.observe(resp.headers)

            if resp.status_code == 429:
                # Safe to retry for every method: the request was rejected, not executed.
                self.limiter.note_429()
                last_retry_after = backoff_seconds(attempt, resp.headers.get("retry-after"))
                if attempt < self.config.max_retries:
                    self.retries_performed += 1
                    await asyncio.sleep(last_retry_after)
                    continue
                raise RateLimitedError(last_retry_after)

            if resp.status_code >= 500 and idempotent and method == "GET" and attempt < self.config.max_retries:
                self.retries_performed += 1
                await asyncio.sleep(backoff_seconds(attempt, resp.headers.get("retry-after")))
                continue

            if resp.status_code >= 400:
                raise self._to_error(resp)
            return resp
        raise RateLimitedError(last_retry_after)  # pragma: no cover

    @staticmethod
    def _to_error(resp: httpx.Response) -> FreshdeskError:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        message = body.get("description") or body.get("message") or resp.reason_phrase
        if resp.status_code == 401:
            message = "authentication failed: check FRESHDESK_API_KEY"
        elif resp.status_code == 403:
            message = "forbidden: API key lacks permission for this resource"
        elif resp.status_code == 404:
            message = "not found"
        return FreshdeskError(resp.status_code, str(message), body.get("errors"))

    async def get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        resp = await self.request("GET", path, params=clean)
        return resp.json()

    async def post(self, path: str, json: Any) -> Any:
        return (await self.request("POST", path, json=json)).json()

    async def put(self, path: str, json: Any) -> Any:
        return (await self.request("PUT", path, json=json)).json()

    async def whoami(self) -> dict:
        """Startup auth validation."""
        return await self.get("/agents/me")
