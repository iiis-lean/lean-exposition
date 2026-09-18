"""Configuration and limits for the unconnected SDK candidate runtime."""
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RuntimeConfig:
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    credential_env: str = "DEEPSEEK_API_KEY"
    transport_timeout: float = 90
    reasoning: str | None = "high"
    protocol: Literal["responses", "chat_completions"] = "responses"
    proxy: Literal["direct", "environment"] = "direct"

    def __post_init__(self):
        url = urlsplit(self.base_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("base_url must be an HTTP(S) endpoint without credentials, query or fragment")
        if not self.model.strip() or not self.credential_env.strip():
            raise ValueError("model and credential_env must be nonempty")
        if self.protocol not in ("responses", "chat_completions"):
            raise ValueError("Unsupported API protocol")
        if self.proxy not in ("direct", "environment"):
            raise ValueError("Unsupported proxy policy")
        if self.reasoning is not None and not self.reasoning.strip():
            raise ValueError("reasoning must be nonempty or None")
        if self.transport_timeout <= 0:
            raise ValueError("transport_timeout must be positive")


@dataclass(frozen=True)
class RunLimits:
    max_turns: int = 8
    max_tool_calls: int = 16
    max_input_characters: int = 200_000
    max_tool_result_characters: int = 40_000
    timeout: float = 180

    def __post_init__(self):
        if self.max_turns < 1 or self.timeout <= 0:
            raise ValueError("max_turns and timeout must be positive")
        if min(self.max_tool_calls, self.max_input_characters, self.max_tool_result_characters) < 0:
            raise ValueError("Character and tool limits cannot be negative")


class BudgetExceeded(RuntimeError):
    """A local budget stopped execution; existing side effects are not undone."""


class SecretInInput(ValueError):
    """The configured credential was found in model-visible material."""


class UnsupportedAgent(ValueError):
    """An agent requests a feature outside this foundation's verified boundary."""
