"""Provider-agnostic LLM layer.

Two adapters cover almost everything:
  * OllamaLLM        - native Ollama (local, or Ollama's hosted API with an API key)
  * OpenAICompatLLM  - any OpenAI-compatible /chat/completions endpoint: OpenAI, Groq, OpenRouter,
                       Together, Gemini (compat endpoint), LM Studio, vLLM, llama.cpp server, Azure proxies...

Use `make_llm()` (env vars or explicit args) to build one. Add another provider by matching `LLM`.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
import ollama


class LLM(Protocol):
    model: str
    is_cloud: bool

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             schema: dict | None = None) -> dict: ...

    def health(self) -> tuple[bool, str]: ...


# name -> (default base URL, uses native Ollama protocol, API key env var hint)
PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama": {"base_url": "http://localhost:11434", "native": True, "key_hint": "OLLAMA_API_KEY (hosted only)"},
    "openai": {"base_url": "https://api.openai.com/v1", "native": False, "key_hint": "OPENAI_API_KEY"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "native": False, "key_hint": "GROQ_API_KEY"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "native": False, "key_hint": "OPENROUTER_API_KEY"},
    "together": {"base_url": "https://api.together.xyz/v1", "native": False, "key_hint": "TOGETHER_API_KEY"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "native": False,
               "key_hint": "GEMINI_API_KEY"},
    "lmstudio": {"base_url": "http://localhost:1234/v1", "native": False, "key_hint": "(none)"},
    "custom": {"base_url": "", "native": False, "key_hint": "LLM_API_KEY"},
}


def _is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or host.endswith(".local")


# --------------------------------------------------------------------------- Ollama

class OllamaLLM:
    def __init__(self, model: str | None = None, host: str | None = None, api_key: str | None = None,
                 temperature: float = 0.1, num_ctx: int = 4096, think: bool = False):
        self.think = think
        self.model = model or os.getenv("OLLAMA_MODEL", "gemma4:latest")
        self.host = host or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.options = {"temperature": temperature, "num_ctx": num_ctx}
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._client = ollama.Client(host=self.host, headers=headers)

    @property
    def is_cloud(self) -> bool:
        return self.model.endswith(":cloud") or "-cloud" in self.model or not _is_local(self.host)

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             schema: dict | None = None) -> dict:
        """Returns {"content": str, "tool_calls": [{"name", "arguments"}]}."""
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "options": self.options}
        if tools:
            kwargs["tools"] = tools
        if schema:
            kwargs["format"] = schema  # structured output
        # Reasoning ("thinking") models are ~6x slower and add nothing for simple classification.
        try:
            resp = self._client.chat(**kwargs, think=self.think)
        except ollama.ResponseError as exc:
            if "think" not in str(exc).lower():
                raise
            resp = self._client.chat(**kwargs)  # model doesn't support the think flag
        msg = resp["message"] if isinstance(resp, dict) else resp.message
        get = (lambda k, d=None: msg.get(k, d)) if isinstance(msg, dict) else (lambda k, d=None: getattr(msg, k, d))
        calls = []
        for tc in get("tool_calls") or []:
            fn = tc["function"] if isinstance(tc, dict) else tc.function
            name = fn["name"] if isinstance(fn, dict) else fn.name
            args = fn["arguments"] if isinstance(fn, dict) else fn.arguments
            calls.append({"name": name, "arguments": dict(args or {})})
        return {"content": get("content") or "", "tool_calls": calls}

    def health(self) -> tuple[bool, str]:
        try:
            models = self._client.list()
            names = [m["model"] if isinstance(m, dict) else m.model for m in
                     (models["models"] if isinstance(models, dict) else models.models)]
        except Exception as exc:  # noqa: BLE001
            return False, f"Ollama not reachable at {self.host}: {type(exc).__name__}"
        if self.model not in names:
            return False, f"Model {self.model!r} not pulled. Run: ollama pull {self.model}"
        return True, "ok"


# ------------------------------------------------------------------ OpenAI-compatible

class LLMError(RuntimeError):
    pass


class OpenAICompatLLM:
    def __init__(self, model: str, base_url: str, api_key: str | None = None,
                 temperature: float = 0.1, timeout: float = 120.0,
                 transport: httpx.BaseTransport | None = None):
        if not model:
            raise ValueError("model is required")
        if not base_url:
            raise ValueError("base_url is required for OpenAI-compatible providers")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout, transport=transport)

    @property
    def is_cloud(self) -> bool:
        return not _is_local(self.base_url)

    # -- message conversion: Ollama-style history -> OpenAI-style history
    @staticmethod
    def _convert(messages: list[dict], schema: dict | None) -> list[dict]:
        out: list[dict] = []
        pending_ids: list[str] = []
        counter = 0
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                calls = []
                pending_ids = []
                for tc in m["tool_calls"]:
                    fn = tc.get("function", tc)
                    counter += 1
                    cid = f"call_{counter}"
                    pending_ids.append(cid)
                    args = fn.get("arguments", {})
                    calls.append({"id": cid, "type": "function", "function": {
                        "name": fn["name"], "arguments": args if isinstance(args, str) else json.dumps(args)}})
                out.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": calls})
            elif m["role"] == "tool":
                cid = pending_ids.pop(0) if pending_ids else f"call_{counter}"
                out.append({"role": "tool", "tool_call_id": cid, "content": m.get("content", "")})
            else:
                out.append({"role": m["role"], "content": m.get("content", "")})
        if schema:  # many providers only honour json_object, so always state the schema in the prompt
            note = ("\n\nRespond with ONLY a JSON object matching this JSON schema:\n"
                    + json.dumps(schema))
            if out and out[0]["role"] == "system":
                out[0] = {**out[0], "content": out[0]["content"] + note}
            else:
                out.insert(0, {"role": "system", "content": note.strip()})
        return out

    def _post(self, body: dict) -> dict:
        for attempt in range(3):
            try:
                resp = self._http.post("/chat/completions", json=body)
            except httpx.TransportError as exc:
                raise LLMError(f"cannot reach {self.base_url}: {type(exc).__name__}") from exc
            if resp.status_code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(min(10.0, float(resp.headers.get("retry-after", 2 ** attempt))))
                continue
            return {"status": resp.status_code, "json": _safe_json(resp), "text": resp.text[:300]}
        raise LLMError("unreachable")  # pragma: no cover

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             schema: dict | None = None) -> dict:
        body: dict[str, Any] = {"model": self.model, "temperature": self.temperature,
                                "messages": self._convert(messages, schema)}
        if tools:
            body["tools"] = tools
        # Structured output, degrading gracefully: json_schema -> json_object -> plain (schema is in the prompt)
        formats: list[dict | None] = [None]
        if schema:
            formats = [{"type": "json_schema", "json_schema": {"name": "result", "schema": schema}},
                       {"type": "json_object"}, None]
        result = None
        for fmt in formats:
            attempt = {**body, **({"response_format": fmt} if fmt else {})}
            result = self._post(attempt)
            if result["status"] == 200:
                break
            if result["status"] not in (400, 422):  # only format-related errors are worth degrading for
                break
        assert result is not None
        if result["status"] != 200:
            raise LLMError(_describe_error(result))
        msg = (result["json"].get("choices") or [{}])[0].get("message", {})
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except ValueError:
                args = {}
            calls.append({"name": fn.get("name", ""), "arguments": args})
        return {"content": msg.get("content") or "", "tool_calls": calls}

    def health(self) -> tuple[bool, str]:
        try:
            resp = self._http.get("/models")
        except httpx.TransportError as exc:
            return False, f"Cannot reach {self.base_url}: {type(exc).__name__}"
        if resp.status_code in (401, 403):
            return False, "API key rejected (check the key and provider)"
        if resp.status_code >= 500:
            return False, f"Provider error {resp.status_code}"
        return True, "ok"  # 404/405 just means the provider has no /models listing


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _describe_error(result: dict) -> str:
    err = result["json"].get("error")
    message = err.get("message") if isinstance(err, dict) else (err or result["text"])
    return f"LLM request failed ({result['status']}): {str(message)[:200]}"


# --------------------------------------------------------------------------- factory

def make_llm(provider: str | None = None, model: str | None = None, base_url: str | None = None,
             api_key: str | None = None) -> LLM:
    """Build an LLM from explicit args, falling back to env vars:
    LLM_PROVIDER, LLM_MODEL, LLM_BASE_URL, LLM_API_KEY (plus provider-specific key vars like OPENAI_API_KEY).
    Defaults to local Ollama so existing setups keep working."""
    provider = (provider or os.getenv("LLM_PROVIDER") or "ollama").lower()
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; choose from {sorted(PROVIDERS)}")
    preset = PROVIDERS[provider]
    base_url = base_url or os.getenv("LLM_BASE_URL") or ""
    key_var = preset["key_hint"].split()[0]
    api_key = api_key or os.getenv("LLM_API_KEY") or (os.getenv(key_var) if key_var.endswith("_KEY") else None)
    if preset["native"]:
        return OllamaLLM(model=model or os.getenv("LLM_MODEL"), host=base_url or None, api_key=api_key)
    return OpenAICompatLLM(model=model or os.getenv("LLM_MODEL") or "", base_url=base_url or preset["base_url"],
                           api_key=api_key)
