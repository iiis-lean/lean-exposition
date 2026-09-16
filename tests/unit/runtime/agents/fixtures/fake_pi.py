#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import threading
import time


MODE = os.environ.get("FAKE_PI_MODE", "normal")
MESSAGES = [{"role": "assistant", "content": [{"type": "text", "text": "historical"}]}]
WRITE_LOCK = threading.Lock()
SETTLED = threading.Event()


def emit(value):
    with WRITE_LOCK:
        sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def reply(request, data=None):
    emit({"type": "response", "id": request["id"], "command": request["type"], "success": True,
          "data": data or {}})


def finish(stop="stop"):
    text = '{"answer":"ok"}'
    message = {"role": "assistant", "stopReason": stop,
               "content": [{"type": "text", "text": text}]}
    MESSAGES.append(message)
    emit({"type": "message_end", "message": message})
    emit({"type": "agent_settled"})
    SETTLED.set()


def delayed_finish():
    if MODE == "tool":
        emit({"type": "tool_execution_start", "toolName": "read", "toolCallId": "call-1"})
        emit({"type": "tool_execution_end", "toolName": "read", "toolCallId": "call-1"})
    time.sleep(0.2 if MODE in {"slow", "cancel"} else 0.01)
    if not SETTLED.is_set():
        finish()


session_dir = sys.argv[sys.argv.index("--session-dir") + 1]
session_file = os.path.join(session_dir, "fake-pi-session.jsonl")
if "--session" in sys.argv:
    session_file = sys.argv[sys.argv.index("--session") + 1]

for line in sys.stdin:
    request = json.loads(line)
    kind = request["type"]
    if kind == "get_state":
        reply(request, {"sessionId": "pi-session", "sessionFile": session_file,
                        "isStreaming": False, "isCompacting": False})
    elif kind == "get_messages":
        reply(request, {"messages": MESSAGES})
    elif kind == "get_session_stats":
        reply(request, {"tokens": {"input": 10, "output": 2}, "cost": 0.1})
    elif kind == "prompt":
        reply(request)
        if MODE == "retry":
            message = {"role": "assistant", "stopReason": "error", "errorMessage": "temporary",
                       "content": []}
            MESSAGES.append(message)
            emit({"type": "message_end", "message": message})
            emit({"type": "auto_retry_start", "attempt": 1})
        else:
            threading.Thread(target=delayed_finish, daemon=True).start()
    elif kind == "abort_retry":
        reply(request)
        emit({"type": "auto_retry_end", "success": False})
        emit({"type": "agent_settled"})
        SETTLED.set()
    elif kind == "abort":
        reply(request)
        if not SETTLED.is_set():
            finish("aborted")
    elif kind in {"steer", "follow_up"}:
        reply(request)
    else:
        reply(request)
