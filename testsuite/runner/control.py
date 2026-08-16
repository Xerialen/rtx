"""Small dual-wire client for the rtx server control channel."""
from __future__ import annotations

import json
import socket
import time
from typing import Any

from . import mpwire


class ControlError(RuntimeError):
    """A control request or wire operation failed."""


_EVENT_NAMES = {
    "Arrived": "arrived",
    "GotoStall": "goto_stall",
    "BotStall": "bot_stall",
    "SeatHeartbeat": "seat_heartbeat",
}


def _vec3(tokens: list[str], offset: int) -> list[float]:
    try:
        return [float(tokens[offset + i]) for i in range(3)]
    except (IndexError, ValueError) as exc:
        raise ControlError("expected three numeric coordinates") from exc


def _parse_verb(command: str) -> Any:
    tokens = command.split()
    if not tokens:
        raise ControlError("empty control command")
    verb = tokens[0].lower()
    try:
        if verb == "status":
            return "Status"
        if verb == "set":
            return {"Set": {"name": tokens[1], "value": " ".join(tokens[2:])}}
        if verb == "teleport":
            #  is always on the wire, zero unless the caller gives one.
            # Upstream grew the field without a serde default (the engine
            # times out old frames instead of rejecting them), and zero is
            # its documented plain placement — while engines from before the
            # field ignore unknown fields, verified live against 817849a.
            # Optional trailing coords exist for reproducing a moving start.
            velocity = _vec3(tokens, 5) if len(tokens) >= 8 else [0.0, 0.0, 0.0]
            return {
                "Teleport": {
                    "bot": int(tokens[1]),
                    "pos": _vec3(tokens, 2),
                    "vel": velocity,
                }
            }
        if verb == "goto":
            return {"Goto": {"bot": int(tokens[1]), "pos": _vec3(tokens, 2)}}
        if verb == "stop":
            return {"Stop": {"bot": int(tokens[1])}}
        if verb == "hold":
            return {"Hold": {"bot": int(tokens[1])}}
        if verb == "planlink":
            return {
                "PlanLink": {
                    "from": _vec3(tokens, 1),
                    "takeoff": _vec3(tokens, 4),
                    "tgt": _vec3(tokens, 7),
                    "v_req": float(tokens[10]),
                }
            }
        if verb == "runcmd":
            return {"RunCmd": {"raw": command[len(tokens[0]):].strip()}}
        if verb == "items":
            return "Items"
        if verb == "get":
            return {"Get": {"name": tokens[1]}}
        if verb == "prep":
            # Set what the bot is carrying before an attempt. The engine has
            # had this since the jump work (`Cmd::Prep`); it was unreachable
            # only because this parser did not know the verb, and the error it
            # raised — "unsupported control verb" — reads like the engine's.
            return {
                "Prep": {
                    "bot": int(tokens[1]),
                    "health": float(tokens[2]),
                    "rockets": float(tokens[3]),
                }
            }
        if verb == "route":
            # route <bot>
            # route query <from> <to> [mask id,id,...]
            if len(tokens) >= 2 and tokens[1].lower() == "query":
                mask = []
                if len(tokens) >= 6 and tokens[4].lower() == "mask":
                    mask = [int(x) for x in tokens[5].split(",") if x]
                return {
                    "Route": {
                        "bot": 0,
                        "from": int(tokens[2]),
                        "to": int(tokens[3]),
                        "mask_links": mask,
                    }
                }
            return {"Route": {"bot": int(tokens[1])}}
        if verb == "fixa":
            # fixa <recipe> <dry-run|apply|undo> [from to] [lock TOKEN]
            payload = {"recipe": tokens[1], "mode": tokens[2], "lock_token": ""}
            i = 3
            if i + 1 < len(tokens) and tokens[i] != "lock":
                payload["from"] = int(tokens[i])
                payload["to"] = int(tokens[i + 1])
                i += 2
            if i < len(tokens) and tokens[i] == "lock":
                payload["lock_token"] = tokens[i + 1]
            return {"Fixa": payload}
    except (IndexError, ValueError) as exc:
        raise ControlError(f"invalid arguments for control verb {verb!r}") from exc
    raise ControlError(f"unsupported control verb {verb!r}")


def _translate_event(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {"ev": _EVENT_NAMES.get(str(event), str(event).lower())}
    name, fields = next(iter(event.items()))
    out = {"ev": _EVENT_NAMES.get(str(name), str(name).lower())}
    if isinstance(fields, dict):
        out.update(fields)
    else:
        out["value"] = fields
    return out


def _response_data(value: Any) -> Any:
    if isinstance(value, str):
        return {value.lower(): True}
    if isinstance(value, dict) and len(value) == 1:
        return next(iter(value.values()))
    return value


def _translate_msg(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise ControlError(f"unexpected control frame: {message!r}")
    if "Event" in message:
        return _translate_event(message["Event"])
    if "Reply" in message:
        reply = message["Reply"]
        result = reply.get("result")
        if isinstance(result, dict) and "Ok" in result:
            return {
                "id": reply.get("id"),
                "ok": True,
                "data": _response_data(result["Ok"]),
            }
        error = result.get("Err") if isinstance(result, dict) else result
        return {"id": reply.get("id"), "ok": False, "error": error}
    raise ControlError(f"unexpected control message: {message!r}")


class Control:
    """One connection with auto, msgpack, or newline-text handshaking."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: float = 30.0,
        protocol: str = "auto",
    ):
        if protocol not in {"auto", "msgpack", "text"}:
            raise ValueError("protocol must be auto, msgpack, or text")
        self.host = host
        self.port = port
        self.timeout = timeout
        self.events: list[dict[str, Any]] = []
        self._socket: socket.socket | None = None
        self._buffer = b""
        self._next_id = 1
        self._msgpack = protocol == "msgpack"
        self.connect(protocol)

    def connect(self, protocol: str = "auto") -> None:
        self.close()
        self.events.clear()
        self._socket = socket.create_connection((self.host, self.port), self.timeout)
        self._buffer = b""
        if protocol == "auto":
            self._msgpack = self._detect_mode()
        else:
            self._msgpack = protocol == "msgpack"

    def _detect_mode(self) -> bool:
        assert self._socket is not None
        try:
            self._socket.sendall(mpwire.pack_frame({"id": 0, "cmd": "Status"}))
            deadline = time.monotonic() + min(self.timeout, 3.0)
            while time.monotonic() < deadline:
                body = self._read_frame(deadline - time.monotonic())
                if body is None:
                    break
                message = mpwire.unpackb(body)
                if isinstance(message, dict) and "Reply" in message:
                    return True
                if isinstance(message, dict) and "Event" in message:
                    self.events.append(_translate_event(message["Event"]))
        except (OSError, ValueError, ControlError):
            pass
        self.close()
        self._socket = socket.create_connection((self.host, self.port), self.timeout)
        self._buffer = b""
        return False

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def _fill(self, deadline: float) -> bool:
        assert self._socket is not None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        self._socket.settimeout(max(remaining, 0.05))
        try:
            chunk = self._socket.recv(65536)
        except (TimeoutError, socket.timeout):
            return False
        if not chunk:
            raise ControlError("control connection closed")
        self._buffer += chunk
        return True

    def _read_frame(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while len(self._buffer) < 4:
            if not self._fill(deadline):
                return None
        size = int.from_bytes(self._buffer[:4], "little")
        if size > mpwire.MAX_FRAME:
            raise ControlError("control frame length too large")
        while len(self._buffer) < size + 4:
            if not self._fill(deadline):
                return None
        body = self._buffer[4:size + 4]
        self._buffer = self._buffer[size + 4:]
        return body

    def _read(self, timeout: float) -> dict[str, Any] | None:
        if self._msgpack:
            body = self._read_frame(timeout)
            return None if body is None else _translate_msg(mpwire.unpackb(body))
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buffer:
            if not self._fill(deadline):
                return None
        line, self._buffer = self._buffer.split(b"\n", 1)
        try:
            return json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise ControlError(f"invalid text control reply: {exc}") from exc

    def request(self, command: str, timeout: float = 15.0) -> dict[str, Any]:
        assert self._socket is not None
        typed = _parse_verb(command)
        request_id = self._next_id
        self._next_id += 1
        if self._msgpack:
            self._socket.sendall(mpwire.pack_frame({"id": request_id, "cmd": typed}))
        else:
            self._socket.sendall(f"{request_id} {command}\n".encode("ascii"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self._read(deadline - time.monotonic())
            if message is None:
                continue
            if "ev" in message:
                self.events.append(message)
            elif message.get("id") == request_id:
                if message.get("ok") is not True:
                    raise ControlError(str(message.get("error", "request failed")))
                return {
                    "id": request_id,
                    "ok": True,
                    "data": message.get("data"),
                }
        raise ControlError(f"request {command!r} timed out")
