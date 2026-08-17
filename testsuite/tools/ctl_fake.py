#!/usr/bin/env python3
"""In-process fake ctl transport speaking rtx-ctlproto msgpack frames.

Deploy tests drive the real ``runner.control.Control`` against this server.
The engine session mirrors 5c66a6d: registered recipes, undo-only plant
handles, ``fixa chain``. No rig.
"""

from __future__ import annotations

import socket
import threading
from typing import Any

from d_deploy import live_to_motor
from d_failclosed import (
    ENGINE_REGISTERED_RECIPES,
    ENGINE_UNDO_HANDLES,
    ENGINE_UNDOABLE,
    planlink_payload_sha256,
)
from runner import mpwire


class EngineRefuse(Exception):
    """Maps to Reply.result.Err on the wire."""


class EngineSession:
    """5c66a6d do_fixa + PlanLink. Typed cmds only (what Control puts on the wire)."""

    REGISTERED = ENGINE_REGISTERED_RECIPES
    UNDO_HANDLES = ENGINE_UNDO_HANDLES
    UNDOABLE = ENGINE_UNDOABLE

    def __init__(
        self,
        idents,
        *,
        corrupt_after=None,
        refuse_apply=None,
        drop_reply_after_commit=None,
        motorbugg_on_fail=False,
    ):
        self.idents = [dict(x) for x in idents]
        self.idx = 0
        self.cmds: list = []
        self.stack: list[str] = []
        self.corrupt_after = corrupt_after
        self.refuse_apply = refuse_apply
        self.drop_reply_after_commit = drop_reply_after_commit
        self.motorbugg_on_fail = motorbugg_on_fail
        self.n_apply = 0
        self.last_planlink: dict[str, Any] | None = None
        self.last_planlink_sha: str | None = None
        self.last_komponat: dict[str, Any] | None = None

    def _cur(self):
        return self.idents[self.idx]

    def _fixa_body(self, recipe: str, mode: str, outcome: str, audit: str = "") -> dict[str, Any]:
        m = live_to_motor(self._cur())
        return {
            "recipe": recipe,
            "mode": mode,
            "outcome": outcome,
            "reason": None,
            "map": "dm3",
            "cells": m["cells"],
            "links": m["links"],
            "rj_links": m["rj_links"],
            "stamp": m["graph_stamp"],
            "content_hash": m["graph_content_hash"],
            "stamp_before": None,
            "stamp_after": m["graph_stamp"],
            "astar_before": None,
            "astar_after": None,
            "astar_next_best": None,
            "audit": audit,
        }

    def handle(self, cmd: Any) -> dict[str, Any]:
        self.cmds.append(cmd)
        if cmd == "Status" or (isinstance(cmd, str) and cmd.lower() == "status"):
            return {"Status": {"map": "dm3", "navmesh": "ready"}}
        if isinstance(cmd, dict) and "PlanLink" in cmd:
            payload = dict(cmd["PlanLink"])
            if not str(payload.get("lock_token") or "").strip():
                raise EngineRefuse("plant requires lock_token")
            self.last_planlink = payload
            self.last_planlink_sha = planlink_payload_sha256(payload)
            return self._push_planlink(payload)
        if isinstance(cmd, dict) and "PlanCell" in cmd:
            if not str((cmd["PlanCell"] or {}).get("lock_token") or "").strip():
                raise EngineRefuse("plant requires lock_token")
            raise EngineRefuse("PlanCell not used by deploy-runner")
        if isinstance(cmd, dict) and "PlanDrop" in cmd:
            if not str((cmd["PlanDrop"] or {}).get("lock_token") or "").strip():
                raise EngineRefuse("plant requires lock_token")
            raise EngineRefuse("PlanDrop not used by deploy-runner")
        if isinstance(cmd, dict) and "Komponat" in cmd:
            return self._komponat(cmd["Komponat"] or {})
        if isinstance(cmd, dict) and "Fixa" in cmd:
            body = cmd["Fixa"]
            return self._fixa(str(body.get("recipe") or ""), str(body.get("mode") or ""))
        raise EngineRefuse(f"unsupported cmd {cmd!r}")

    def _chain(self) -> dict[str, Any]:
        if not self.stack:
            audit = "rtx: navpatch undo chain: empty (live graph is this session's base)\n"
            return {"Fixa": self._fixa_body("", "chain", "empty", audit)}
        top = self.stack[-1]
        names = " -> ".join(self.stack)
        audit = (
            f"rtx: navpatch undo chain: depth={len(self.stack)} [{names}], "
            f"next undo={top}\n"
        )
        body = self._fixa_body(top, "chain", "chained", audit)
        return {"Fixa": body}

    def _fixa(self, rid: str, mode: str) -> dict[str, Any]:
        if mode == "chain":
            return self._chain()
        if mode == "undo":
            if rid not in self.UNDOABLE:
                raise EngineRefuse(
                    f"unknown undo target {rid!r}; undoable: {list(self.UNDOABLE)}"
                )
            if not self.stack:
                raise EngineRefuse("no apply snapshot — nothing to undo")
            if self.stack[-1] != rid:
                raise EngineRefuse(
                    f"top of the undo chain is '{self.stack[-1]}', not '{rid}' "
                    f"— undo in reverse order"
                )
            self.stack.pop()
            if self.idx <= 0:
                raise EngineRefuse("undo under bas")
            self.idx -= 1
            return {"Fixa": self._fixa_body(rid, "undo", "undone")}
        if rid not in self.REGISTERED:
            raise EngineRefuse(
                f"unknown recipe {rid!r}; registered: {list(self.REGISTERED)}"
            )
        if mode == "dry-run":
            return {"Fixa": self._fixa_body(rid, "dry-run", "dry_run_ok")}
        if mode == "apply":
            if self.refuse_apply and rid == self.refuse_apply:
                raise EngineRefuse(f"steg {rid} vägrad")
            self._push(rid)
            return {"Fixa": self._fixa_body(rid, "apply", "applied")}
        raise EngineRefuse(f"fixa mode {mode!r} (want dry-run|apply|undo|chain)")

    def _push(self, name: str) -> dict[str, Any]:
        self.n_apply += 1
        self.stack.append(name)
        self.idx = min(self.idx + 1, len(self.idents) - 1)
        return dict(self.idents[self.idx])

    def _ident(self, live: dict[str, Any] | None = None) -> dict[str, Any]:
        m = live_to_motor(live if live is not None else self._cur())
        return {
            "cells": m["cells"],
            "links": m["links"],
            "rj_links": m["rj_links"],
            "graph_stamp": m["graph_stamp"],
            "graph_content_hash": m["graph_content_hash"],
        }

    def _komponat_refused(self, body: dict[str, Any], reason: str, steps=None):
        return {
            "Komponat": {
                "recept_id": str(body.get("recept_id") or ""),
                "outcome": "refused",
                "reason": reason,
                "base": body.get("base") or self._ident(),
                "observed_final": self._ident(),
                "steps": list(steps or []),
                "undo_name": "komponat",
                "audit": f"rtx: komponat refused ({reason}) — live graph untouched\n",
            }
        }

    def _komponat(self, body: dict[str, Any]) -> dict[str, Any]:
        """Atomic compose matching rtx-ctlproto Cmd::Komponat."""
        self.last_komponat = dict(body)
        if not str(body.get("lock_token") or "").strip():
            raise EngineRefuse("apply requires lock_token (rig-lock)")
        steps_in = list(body.get("steps") or [])
        planned: list[tuple[str, str, dict[str, Any]]] = []
        for step in steps_in:
            if not isinstance(step, dict):
                return self._komponat_refused(body, f"unsupported step {step!r}")
            name = str(step.get("name") or "")
            op = step.get("op") or {}
            if not isinstance(op, dict):
                return self._komponat_refused(body, f"unsupported op {op!r}")
            if "PlanLink" in op:
                payload = dict(op["PlanLink"] or {})
                planned.append((name, "plan-link", payload))
            elif "Recipe" in op:
                rid = str((op["Recipe"] or {}).get("name") or "")
                if rid not in self.REGISTERED:
                    return self._komponat_refused(
                        body, f"unknown recipe {rid!r}"
                    )
                if self.refuse_apply and rid == self.refuse_apply:
                    done = [
                        {"name": n, "outcome": "ok", "reason": None,
                         "observed": None, "link": None}
                        for n, _, _ in planned
                    ]
                    done.append({
                        "name": name, "outcome": "refused",
                        "reason": f"steg {rid} vägrad",
                        "observed": None, "link": None,
                    })
                    return self._komponat_refused(body, f"steg {rid} vägrad", done)
                planned.append((name, rid, {}))
            else:
                return self._komponat_refused(body, f"unsupported komponat op {op!r}")
        if not planned:
            return self._komponat_refused(body, "komponat without steps — nothing to apply")
        results: list[dict[str, Any]] = []
        for name, kind, payload in planned:
            if kind == "plan-link":
                self.last_planlink = payload
                self.last_planlink_sha = planlink_payload_sha256(payload)
                self._push("plan-link")
                link: int | None = 48131
            else:
                self._push(kind)
                link = None
            results.append({
                "name": name,
                "outcome": "ok",
                "reason": None,
                "observed": self._ident(),
                "link": link,
            })
        if self.drop_reply_after_commit:
            raise EngineRefuse("reply lost after commit")
        if self.corrupt_after:
            ident = dict(self.idents[self.idx])
            ident["graph_content_hash"] = "ab" * 32
            self.idents[self.idx] = ident
        return {
            "Komponat": {
                "recept_id": str(body.get("recept_id") or ""),
                "outcome": "applied",
                "reason": None,
                "base": body.get("base") or self._ident(self.idents[0]),
                "observed_final": self._ident(),
                "steps": results,
                "undo_name": "komponat",
                "audit": "rtx: komponat applied\n",
            }
        }

    def _push_planlink(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._push("plan-link")
        return {
            "PlanLink": {
                "link": 48131,
                "from_cell": 1167,
                "to_cell": 1191,
                "from": payload.get("from"),
                "tgt": payload.get("tgt"),
                "takeoff": payload.get("takeoff"),
                "v_req": payload.get("v_req"),
                "airtime": 0.4,
                "cost": 1.0,
            }
        }


class FakeCtlServer:
    """One listening socket. Speaks ``[u32le len][msgpack {id,cmd}/{Reply}]``."""

    def __init__(self, engine: EngineSession):
        self.engine = engine
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self._sock.settimeout(0.2)
        self.port = int(self._sock.getsockname()[1])
        self._thread = threading.Thread(target=self._run, name="fake-ctl", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._serve(conn)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _serve(self, conn: socket.socket) -> None:
        conn.settimeout(0.5)
        buf = b""
        while not self._stop.is_set():
            while len(buf) < 4:
                try:
                    chunk = conn.recv(65536)
                except (TimeoutError, socket.timeout):
                    if self._stop.is_set():
                        return
                    continue
                if not chunk:
                    return
                buf += chunk
            size = int.from_bytes(buf[:4], "little")
            if size > mpwire.MAX_FRAME:
                return
            while len(buf) < size + 4:
                try:
                    chunk = conn.recv(65536)
                except (TimeoutError, socket.timeout):
                    if self._stop.is_set():
                        return
                    continue
                if not chunk:
                    return
                buf += chunk
            body = buf[4:size + 4]
            buf = buf[size + 4:]
            try:
                msg = mpwire.unpackb(body)
            except Exception:
                return
            if not isinstance(msg, dict):
                continue
            rid = msg.get("id")
            cmd = msg.get("cmd")
            try:
                ok = self.engine.handle(cmd)
                reply = {"Reply": {"id": rid, "result": {"Ok": ok}}}
            except EngineRefuse as exc:
                reply = {"Reply": {"id": rid, "result": {"Err": str(exc)}}}
            try:
                conn.sendall(mpwire.pack_frame(reply))
            except OSError:
                return

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)
