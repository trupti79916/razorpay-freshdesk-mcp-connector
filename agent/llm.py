"""Thin LLM adapter. Ollama implementation; swap in another provider by matching `LLM`."""
from __future__ import annotations

import os
from typing import Any, Protocol

import ollama


class LLM(Protocol):
    model: str

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             schema: dict | None = None) -> dict: ...

    def health(self) -> tuple[bool, str]: ...


class OllamaLLM:
    def __init__(self, model: str | None = None, host: str | None = None,
                 temperature: float = 0.1, num_ctx: int = 4096, think: bool = False):
        self.think = think
        self.model = model or os.getenv("OLLAMA_MODEL", "gemma4:latest")
        self.host = host or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.options = {"temperature": temperature, "num_ctx": num_ctx}
        self._client = ollama.Client(host=self.host)

    @property
    def is_cloud(self) -> bool:
        return self.model.endswith(":cloud") or "-cloud" in self.model

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
