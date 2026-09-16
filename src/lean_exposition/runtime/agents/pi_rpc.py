from __future__ import annotations

import json
import subprocess
import threading
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from time import monotonic


class PiRpcError(RuntimeError):
    pass


class PiDeliveryUnknown(PiRpcError):
    """The command may have reached Pi, so callers must not replay it."""


class PiRpcProcess:
    """Small LF-delimited JSON RPC client for ``pi --mode rpc``."""

    def __init__(self, command: list[str], *, cwd: Path, env: Mapping[str, str]) -> None:
        self.command_line = tuple(command)
        self.process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._responses: dict[str, dict[str, object]] = {}
        self._records: list[dict[str, object]] = []
        self._stderr: list[str] = []
        self._error: BaseException | None = None
        self._closed = False
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start()
        self._stderr_reader.start()

    @property
    def records(self) -> tuple[dict[str, object], ...]:
        with self._condition:
            return tuple(self._records)

    def command(
        self,
        command_type: str,
        payload: Mapping[str, object] | None = None,
        *,
        timeout: float = 15.0,
    ) -> dict[str, object]:
        request_id = f"lex-{uuid.uuid4().hex}"
        self._write({"id": request_id, "type": command_type, **dict(payload or {})})
        deadline = monotonic() + timeout
        with self._condition:
            while request_id not in self._responses:
                self._raise_if_unusable()
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise PiDeliveryUnknown(f"Pi RPC response unavailable: {command_type}")
                self._condition.wait(min(remaining, 0.1))
            response = self._responses.pop(request_id)
        if response.get("type") != "response" or response.get("command") != command_type:
            raise PiRpcError(f"Pi RPC returned a mismatched response for {command_type}")
        if response.get("success") is not True:
            raise PiRpcError(f"Pi RPC rejected {command_type}")
        data = response.get("data")
        return data if isinstance(data, dict) else {}

    def wait_for(
        self,
        predicate: Callable[[dict[str, object]], bool],
        *,
        after: int = 0,
        timeout: float,
        observe: Callable[[dict[str, object]], None] | None = None,
    ) -> tuple[dict[str, object], int]:
        deadline = monotonic() + timeout
        cursor = max(after, 0)
        with self._condition:
            while True:
                while cursor < len(self._records):
                    record = self._records[cursor]
                    cursor += 1
                    if observe is not None:
                        observe(record)
                    if predicate(record):
                        return record, cursor
                self._raise_if_unusable()
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Pi agent did not settle before the deadline")
                self._condition.wait(min(remaining, 0.1))

    def close(self, *, timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        if self.process.stdin is not None and not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        with self._condition:
            self._condition.notify_all()

    def terminate(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        self.close(timeout=2)

    def _write(self, record: Mapping[str, object]) -> None:
        if self._closed or self.process.stdin is None or self.process.stdin.closed:
            raise PiRpcError("Pi RPC process is closed")
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        try:
            with self._write_lock:
                self.process.stdin.write(encoded + "\n")
                self.process.stdin.flush()
        except OSError as exc:
            raise PiDeliveryUnknown("Pi RPC write outcome is unknown") from exc

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                if len(line.encode("utf-8")) > 4 * 1024 * 1024:
                    raise PiRpcError("Pi RPC frame exceeded 4 MiB")
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise PiRpcError("Pi RPC stdout contained non-JSON data") from exc
                if not isinstance(value, dict):
                    raise PiRpcError("Pi RPC stdout record must be an object")
                with self._condition:
                    response_id = value.get("id")
                    if value.get("type") == "response" and isinstance(response_id, str):
                        if response_id in self._responses:
                            raise PiRpcError("Pi RPC returned a duplicate response id")
                        self._responses[response_id] = value
                    self._records.append(value)
                    self._condition.notify_all()
        except BaseException as exc:
            with self._condition:
                self._error = exc
                self._condition.notify_all()
        finally:
            with self._condition:
                self._condition.notify_all()

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            with self._condition:
                self._stderr.append(line)
                if sum(map(len, self._stderr)) > 8192:
                    self._stderr = ["".join(self._stderr)[-4096:]]

    def _raise_if_unusable(self) -> None:
        if self._error is not None:
            raise PiRpcError(type(self._error).__name__) from self._error
        returncode = self.process.poll()
        if returncode is not None:
            raise PiRpcError(f"Pi RPC exited with code {returncode}")
