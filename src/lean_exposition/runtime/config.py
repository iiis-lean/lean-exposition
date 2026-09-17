from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ApiConfig:
    """Configuration for one OpenAI-compatible API endpoint."""

    model: str
    credential_env: str
    base_url: str | None = None
    protocol: str = "responses"
    transport: str = "http"
    proxy_url: str | None = None
    timeout: float = 180.0
    max_output_tokens: int | None = None
    reasoning: dict[str, Any] | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)
    prompt_cache_key: str | None = None

    def __post_init__(self) -> None:
        if not self.model:
            raise ValueError("model is required")
        if not self.credential_env:
            raise ValueError("credential_env is required")
        if self.protocol not in {"responses", "chat_completions"}:
            raise ValueError("protocol must be responses or chat_completions")
        if self.transport != "http":
            raise ValueError("direct API execution currently supports only HTTP transport")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive when set")
        if self.protocol == "chat_completions" and self.reasoning:
            unsupported = set(self.reasoning) - {"effort"}
            if unsupported:
                raise ValueError("Chat Completions accepts only reasoning.effort")
        if self.base_url:
            from urllib.parse import urlparse

            if (
                urlparse(self.base_url).hostname == "api.deepseek.com"
                and self.model != "deepseek-flash"
            ):
                raise ValueError("The official DeepSeek endpoint is restricted to deepseek-flash")
        object.__setattr__(self, "reasoning", copy.deepcopy(self.reasoning))
        object.__setattr__(self, "extra_body", copy.deepcopy(self.extra_body))
