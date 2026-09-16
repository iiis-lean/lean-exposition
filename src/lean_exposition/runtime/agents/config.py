from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


_UNSAFE_PI_TOOLS = frozenset({"bash", "edit", "write", "powershell"})
_SAFE_PI_BUILTINS = frozenset({"read", "grep", "find", "ls"})


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    url: str
    enabled_tools: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name or not self.url or not self.enabled_tools:
            raise ValueError("MCP server name, URL and enabled_tools are required")
        object.__setattr__(self, "enabled_tools", tuple(dict.fromkeys(self.enabled_tools)))


@dataclass(frozen=True)
class CodexAgentConfig:
    model: str
    cwd: str
    reasoning: str = "high"
    timeout: float = 900.0
    codex_home: str | None = None
    auth_path: str | None = None
    codex_bin: str | None = None
    sdk_python_root: str | None = None
    developer_instructions: str | None = None
    codex_config: Mapping[str, Any] = field(default_factory=dict)
    mcp_servers: tuple[McpServerConfig, ...] = ()

    def __post_init__(self) -> None:
        if not self.model or not self.cwd:
            raise ValueError("model and cwd are required")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if "deepseek" in self.model.casefold() and "flash" not in self.model.casefold():
            raise ValueError("DeepSeek Agent execution only permits a Flash model")
        if self.auth_path is not None and not Path(self.auth_path).is_file():
            raise ValueError("auth_path must identify an existing file")
        hidden_resources = (
            key for key in self.codex_config
            if str(key).startswith(("mcp_servers", "skills", "plugins"))
        )
        if next(hidden_resources, None) is not None:
            raise ValueError("MCP, skill and plugin resources require an explicit Agent field")
        servers = tuple(
            item if isinstance(item, McpServerConfig) else McpServerConfig(**item)
            for item in self.mcp_servers
        )
        object.__setattr__(self, "codex_config", copy.deepcopy(dict(self.codex_config)))
        object.__setattr__(self, "mcp_servers", servers)


@dataclass(frozen=True)
class PiAgentConfig:
    model: str
    provider: str
    cwd: str
    session_dir: str
    reasoning: str = "high"
    timeout: float = 900.0
    executable: str = "pi"
    credential_env: str | None = None
    provider_credential_env: str | None = None
    tools: tuple[str, ...] = ()
    system_prompt: str | None = None

    def __post_init__(self) -> None:
        if not self.model or not self.provider or not self.cwd or not self.session_dir:
            raise ValueError("model, provider, cwd and session_dir are required")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.provider.casefold() == "deepseek" and "flash" not in self.model.casefold():
            raise ValueError("DeepSeek Agent execution only permits a Flash model")
        tools = tuple(dict.fromkeys(self.tools))
        unsafe = _UNSAFE_PI_TOOLS.intersection(tools)
        if unsafe:
            raise ValueError("Pi shell and file-write tools are not permitted")
        unknown_builtins = {
            name for name in tools if name in {"read", "grep", "find", "ls", *_UNSAFE_PI_TOOLS}
            and name not in _SAFE_PI_BUILTINS
        }
        if unknown_builtins:
            raise ValueError("unsupported Pi tool selection")
        if self.credential_env and not self.provider_credential_env:
            raise ValueError("provider_credential_env is required with credential_env")
        Path(self.cwd)
        Path(self.session_dir)
        object.__setattr__(self, "tools", tools)


def safe_pi_builtin_tools() -> frozenset[str]:
    return _SAFE_PI_BUILTINS
