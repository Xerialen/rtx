#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Loopback-only mvdsv/KTX bench lifecycle for MLX Phase 1."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


MATCH_PORT_MIN = 28600
MATCH_PORT_MAX = 28700


def validate_match_port(port: int) -> int:
    """Reject match-server ports outside the Phase 1 allocation."""
    if not MATCH_PORT_MIN <= port <= MATCH_PORT_MAX:
        raise ValueError(f"match port must be in {MATCH_PORT_MIN}-{MATCH_PORT_MAX}: {port}")
    return port


def _quake_text(data: bytes) -> str:
    """Decode Quake text while removing the high-bit colour flag."""
    return bytes(byte & 0x7F for byte in data).decode("latin-1", errors="replace")


def parse_status_packet(packet: bytes) -> dict[str, object]:
    """Parse an mvdsv ``status`` connectionless reply."""
    if packet.startswith(b"\xff\xff\xff\xff"):
        packet = packet[4:]
    text = _quake_text(packet).replace("\r", "")
    lines = text.splitlines()
    if not lines:
        raise ValueError("empty status response")
    info = lines[0]
    if info.startswith("n"):
        info = info[1:]
    if not info.startswith("\\"):
        raise ValueError("status response has no server info string")
    fields = info.split("\\")[1:]
    if len(fields) % 2:
        raise ValueError("status response has an incomplete server info string")
    result: dict[str, object] = dict(zip(fields[::2], fields[1::2], strict=True))
    result["players"] = [line for line in lines[1:] if line.strip(" \t\r\n\x00")]
    return result


def build_server_config(run_id: str, port: int, timelimit: int) -> str:
    """Return the proven KTX 4on4 profile, hardened for one external squad."""
    lines = [
        f"// MLX Phase 1 KTX 4on4 config {run_id}",
        "setmaster",
        f'hostname "mlx:{port}"',
        f'set k_motd1 "MLX Phase 1 {run_id}"',
        "set k_matchless 0",
        "set k_use_matchless_dir 1",
        "set k_allowed_free_modes 4095",
        "set k_defmode 4on4",
        "set k_free_mode 5",
        "set k_mode 2",
        "set k_defmap dm3",
        "set k_fb_enabled 1",
        "set k_fb_autoadd_limit 0",
        "set k_fb_autoremove_at 0",
        "set k_fb_auto_delay 1",
        "set k_fb_skill 20",
        "set k_count 0",
        "set k_auto_xonx 0",
        "set k_lockmap 1",
        "coop 0",
        "maxclients 9",
        "set k_maxclients 8",
        "deathmatch 1",
        "teamplay 2",
        f"timelimit {timelimit}",
        "fraglimit 0",
        "samelevel 1",
        "set k_membercount 4",
        "set k_lockmin 1",
        "set k_lockmax 2",
        "set k_overtime 0",
        "set k_exttime 0",
        "set k_noframechecks 1",
        "set sv_public 0",
        "set sv_getrealip 0",
        "set sv_login 0",
        "set sv_timeout 3600",
        "set sv_rconlim 100",
        "sv_mapcheck 0",
        "set k_idletime 0",
        "set demo_tmp_record 1",
        "set k_demo_mintime 0",
        "set k_demotxt_format json",
        "sv_demotxt 2",
        "sv_demofps 77",
        f"sv_demodir demos_p{port}",
        f"set qtv_streamport {port}",
        "set qtv_maxstreams 0",
        'set qtv_password ""',
    ]
    return "\n".join(lines) + "\n"


def server_argv(serverdir: Path, mvdsv: Path, port: int, config_name: str) -> list[str]:
    """Build an mvdsv argv that cannot bind beyond loopback."""
    del serverdir  # The caller uses it as cwd; keeping it explicit documents the contract.
    return [
        str(mvdsv),
        "-ip",
        "127.0.0.1",
        "-port",
        str(port),
        "-mem",
        "64",
        "-game",
        "ktx",
        "-progtype",
        "1",
        "+exec",
        config_name,
        "+map",
        "dm3",
    ]


def client_argv(
    binary: Path,
    serverdir: Path,
    port: int,
    control_port: int,
    *,
    team: str,
    bhop: bool,
    auto_ready: bool = True,
) -> list[str]:
    """Build the single official rtx-client process that carries four MLX bots."""
    argv = [
        str(binary),
        "--server",
        f"127.0.0.1:{port}",
        "--basedir",
        str(serverdir),
        "--bots",
        "4",
        "--name",
        "mlx",
        "--team",
        team,
        "--colors",
        "4",
        "4",
        "--skill",
        "7",
        "--no-download",
    ]
    if not auto_ready:
        argv.append("--no-auto-ready")
    argv.extend([
        "--control-port", str(control_port),
        "+set",
        "rtx_bot_bhop",
        "1" if bhop else "0",
    ])
    return argv


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def check_prerequisites(serverdir: Path, mvdsv: Path, rtx_client: Path, qw_min_client: Path) -> None:
    required = (
        mvdsv,
        rtx_client,
        qw_min_client,
        serverdir / "ktx" / "qwprogs.so",
        serverdir / "id1" / "maps" / "dm3.bsp",
        serverdir / "ktx" / "bots" / "maps" / "dm3.bot",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing MLX server prerequisite(s): " + ", ".join(missing))
    for executable in (mvdsv, rtx_client):
        if not os.access(executable, os.X_OK):
            raise PermissionError(f"not executable: {executable}")


def assert_udp_port_free(port: int) -> None:
    validate_match_port(port)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", port))


def assert_tcp_port_free(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


def query_status(port: int, timeout: float = 1.0) -> dict[str, object]:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(b"\xff\xff\xff\xffstatus\n", ("127.0.0.1", port))
        packet, address = sock.recvfrom(65535)
    status = parse_status_packet(packet)
    status["address"] = f"{address[0]}:{address[1]}"
    return status


def wait_for_player_count(
    port: int,
    expected: int,
    deadline: float,
    process: subprocess.Popen[bytes],
) -> dict[str, object]:
    last = "no status reply"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"mvdsv exited before roster reached {expected}: {process.returncode}")
        try:
            status = query_status(port)
            count = len(status["players"])
            if count == expected:
                return status
            last = f"observed {count} players"
        except (OSError, ValueError) as exc:
            last = str(exc)
        time.sleep(0.25)
    raise TimeoutError(f"roster did not reach exactly {expected}: {last}")


def send_console(process: subprocess.Popen[bytes], command: str, settle: float = 0.15) -> None:
    if process.stdin is None or process.poll() is not None:
        raise RuntimeError(f"cannot send mvdsv console command: {command}")
    process.stdin.write(command.encode("ascii") + b"\n")
    process.stdin.flush()
    time.sleep(settle)


def wait_for_log(
    log_path: Path,
    phrase: str,
    deadline: float,
    process: subprocess.Popen[bytes],
) -> None:
    phrase = phrase.casefold()
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"mvdsv exited while waiting for log marker {phrase!r}: {process.returncode}")
        if log_path.is_file():
            text = log_path.read_text(encoding="latin-1", errors="replace").casefold()
            if phrase in text:
                return
        time.sleep(0.25)
    raise TimeoutError(f"server log did not contain {phrase!r}")


def spawn_frogbots(qw_min_client: Path, port: int, count: int = 4) -> None:
    """Use the proven narrow QW client to issue KTX commands before MLX joins."""
    spec = importlib.util.spec_from_file_location("mlx_qw_min_client", qw_min_client)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load QW command client: {qw_min_client}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    commands = ["botcmd addbot 20 frogs"] * count
    control = module.QWMinClient(
        host="127.0.0.1",
        port=port,
        local_port=0,
        run_for=60,
        bot_count=0,
        bot_spacing=0.75,
        name="MLXControl",
        team=None,
        spectator=True,
        verbose=False,
        botcmds=[],
        commands=[],
    )
    try:
        control.connect()
        signed_on = False
        sent = 0
        next_command = 0.0
        next_nop = 0.0
        deadline = time.time() + 30
        while time.time() < deadline and sent < len(commands):
            try:
                packet, _source = control.sock.recvfrom(8192)
                control.process_packet(packet)
            except socket.timeout:
                pass
            now = time.time()
            if control.spawncount is not None and not signed_on:
                control.send_reliable(
                    [
                        f"prespawn {control.spawncount} 0 0",
                        f"spawn {control.spawncount} 0",
                        f"begin {control.spawncount}",
                        commands[0],
                    ]
                )
                signed_on = True
                sent = 1
                next_command = now + 0.75
                next_nop = now + 0.5
            elif signed_on and sent < len(commands) and now >= next_command:
                control.send_reliable([commands[sent]])
                sent += 1
                next_command = now + 0.75
            if signed_on and now >= next_nop:
                control.send_nop()
                next_nop = now + 0.5
        if sent != len(commands):
            raise RuntimeError(f"only transmitted {sent}/{len(commands)} Frogbot commands")
        time.sleep(1)
    finally:
        control.sock.close()


def verify_client_barrier(control_port: int, deadline: float) -> dict[str, object]:
    """Require the armed squad's control socket to accept one command before relaunch."""
    last: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", control_port), timeout=1) as connection:
                connection.settimeout(4)
                connection.sendall(b"1 cmd ready\n")
                buffer = b""
                while b"\n" not in buffer:
                    chunk = connection.recv(4096)
                    if not chunk:
                        raise ConnectionError("control socket closed before receipt")
                    buffer += chunk
                for line in buffer.splitlines():
                    reply = json.loads(line)
                    if reply.get("id") == 1:
                        if not reply.get("ok"):
                            raise RuntimeError(f"rtx-client rejected match barrier: {reply}")
                        return reply
        except OSError as exc:
            last = exc
            time.sleep(0.2)
    raise TimeoutError(f"rtx-client control barrier unavailable: {last}")


def userids(status: dict[str, object]) -> set[int]:
    values: set[int] = set()
    for player in status["players"]:
        try:
            values.add(int(str(player).split()[0]))
        except (IndexError, ValueError):
            continue
    return values


def process_receipt(process: subprocess.Popen[bytes], label: str) -> dict[str, object]:
    stat = Path(f"/proc/{process.pid}/stat").read_text(encoding="utf-8").split()
    return {
        "label": label,
        "pid": process.pid,
        "pgid": os.getpgid(process.pid),
        "startTimeTicks": stat[21],
        "startedAt": utc_now(),
    }


def terminate_owned(
    process: subprocess.Popen[bytes] | None,
    receipt: dict[str, object] | None,
    *,
    kill_group: bool = True,
) -> None:
    if process is None or receipt is None or process.poll() is not None:
        return
    stat_path = Path(f"/proc/{process.pid}/stat")
    try:
        current_ticks = stat_path.read_text(encoding="utf-8").split()[21]
    except (FileNotFoundError, ProcessLookupError):
        return
    if process.pid != receipt["pid"] or current_ticks != receipt["startTimeTicks"]:
        raise RuntimeError(f"refusing to terminate reused or unowned PID {process.pid}")
    if kill_group:
        os.killpg(int(receipt["pgid"]), signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if kill_group:
            os.killpg(int(receipt["pgid"]), signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=3)


def copy_latest_demo(serverdir: Path, port: int, session_dir: Path, started: float) -> Path:
    demo_dir = serverdir / "ktx" / f"demos_p{port}"
    candidates = [
        path
        for path in demo_dir.glob("*.mvd")
        if path.stat().st_size > 0 and path.stat().st_mtime >= started - 5
    ]
    if not candidates:
        raise FileNotFoundError(f"no non-empty fresh MVD found in {demo_dir}")
    source = max(candidates, key=lambda path: path.stat().st_mtime)
    target = session_dir / "demo.mvd"
    shutil.copyfile(source, target)
    for suffix, name in ((".json", "ktxstats.json"), (".txt", "demo.txt")):
        sidecar = source.with_suffix(suffix)
        if sidecar.is_file():
            shutil.copyfile(sidecar, session_dir / name)
    return target


def run_match(args: argparse.Namespace) -> int:
    validate_match_port(args.port)
    serverdir = args.serverdir.resolve()
    mvdsv = args.mvdsv.resolve() if args.mvdsv else serverdir / "mvdsv"
    rtx_client = args.rtx_client.resolve()
    qw_min_client = args.qw_min_client.resolve()
    session_dir = args.session_dir.resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    check_prerequisites(serverdir, mvdsv, rtx_client, qw_min_client)
    assert_udp_port_free(args.port)
    assert_tcp_port_free(args.control_port)

    cfg_name = f"mlx_{args.port}.cfg"
    cfg_path = serverdir / "ktx" / cfg_name
    demo_dir = serverdir / "ktx" / f"demos_p{args.port}"
    demo_dir.mkdir(parents=True, exist_ok=True)
    cfg_tmp = cfg_path.with_suffix(".cfg.tmp")
    cfg_tmp.write_text(build_server_config(args.run_id, args.port, args.timelimit), encoding="utf-8")
    cfg_tmp.replace(cfg_path)

    started = time.time()
    server: subprocess.Popen[bytes] | None = None
    client: subprocess.Popen[bytes] | None = None
    server_receipt: dict[str, object] | None = None
    client_receipt: dict[str, object] | None = None
    server_log_handle = None
    client_log_handle = None
    result: dict[str, object] = {
        "schema": "mlx.match-result.v1",
        "runId": args.run_id,
        "port": args.port,
        "controlPort": args.control_port,
        "bhop": bool(args.bhop),
        "startedAt": utc_now(),
        "ok": False,
    }
    inner_groups = not args.inherit_process_group
    try:
        server_log_path = session_dir / "server.log"
        client_log_path = session_dir / "rtx-client.log"
        server_log_handle = server_log_path.open("wb")
        server = subprocess.Popen(
            server_argv(serverdir, mvdsv, args.port, cfg_name),
            cwd=serverdir,
            stdin=subprocess.PIPE,
            stdout=server_log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=inner_groups,
        )
        server_receipt = process_receipt(server, "mvdsv")
        write_json(session_dir / "process-receipts.json", {"server": server_receipt})
        wait_for_player_count(args.port, 0, time.monotonic() + 20, server)
        for command in (
            "map dm3",
            f"timelimit {args.timelimit}",
            "set k_free_mode 5",
            "set k_membercount 4",
            "set k_noframechecks 1",
            f"sv_demoeasyrecord mlx_{args.run_id}",
        ):
            send_console(server, command, 0.25)

        client_log_handle = client_log_path.open("wb")
        client = subprocess.Popen(
            client_argv(
                rtx_client,
                serverdir,
                args.port,
                args.control_port,
                team="mlx",
                bhop=args.bhop,
                auto_ready=False,
            ),
            cwd=serverdir,
            stdin=subprocess.DEVNULL,
            stdout=client_log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=inner_groups,
        )
        client_receipt = process_receipt(client, "rtx-client")
        write_json(
            session_dir / "process-receipts.json",
            {"server": server_receipt, "client": client_receipt},
        )
        mlx_armed = wait_for_player_count(args.port, 4, time.monotonic() + 40, server)
        write_json(session_dir / "roster-mlx-armed.json", mlx_armed)
        mlx_userids = userids(mlx_armed)

        spawn_frogbots(qw_min_client, args.port)
        armed = wait_for_player_count(args.port, 8, time.monotonic() + 30, server)
        write_json(session_dir / "roster-8-armed.json", armed)
        write_json(
            session_dir / "barrier-receipt.json",
            verify_client_barrier(args.control_port, time.monotonic() + 15),
        )

        terminate_owned(client, client_receipt, kill_group=inner_groups)
        client = None
        client_receipt = None
        try:
            frogs = wait_for_player_count(args.port, 4, time.monotonic() + 8, server)
        except TimeoutError:
            live = query_status(args.port)
            for userid in sorted(userids(live) & mlx_userids):
                send_console(server, f"kick {userid}", 0.1)
            frogs = wait_for_player_count(args.port, 4, time.monotonic() + 8, server)
        write_json(session_dir / "roster-frogs.json", frogs)
        control_deadline = time.monotonic() + 8
        while True:
            try:
                assert_tcp_port_free(args.control_port)
                break
            except OSError:
                if time.monotonic() >= control_deadline:
                    raise TimeoutError("rtx-client control port stayed busy after armed squad stop")
                time.sleep(0.1)

        client = subprocess.Popen(
            client_argv(
                rtx_client,
                serverdir,
                args.port,
                args.control_port,
                team="mlx",
                bhop=args.bhop,
                auto_ready=True,
            ),
            cwd=serverdir,
            stdin=subprocess.DEVNULL,
            stdout=client_log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=inner_groups,
        )
        client_receipt = process_receipt(client, "rtx-client-auto-ready")
        write_json(
            session_dir / "process-receipts.json",
            {"server": server_receipt, "client": client_receipt},
        )
        reconnect_deadline = time.monotonic() + 40
        eight = None
        while time.monotonic() < reconnect_deadline:
            if client.poll() is not None:
                raise RuntimeError(f"auto-ready rtx-client exited during reconnect: {client.returncode}")
            send_console(server, f"timelimit {args.timelimit}", 0.05)
            send_console(server, "set k_overtime 0", 0.05)
            try:
                live = query_status(args.port)
            except (OSError, ValueError):
                continue
            if len(live["players"]) == 8:
                eight = live
                break
        if eight is None:
            raise TimeoutError("auto-ready squad did not restore the exact eight-player roster")
        for _ in range(3):
            send_console(server, f"timelimit {args.timelimit}", 0.05)
        write_json(session_dir / "roster-8.json", eight)

        wait_for_log(server_log_path, "The match has begun!", time.monotonic() + 45, server)
        result["matchStartedAt"] = utc_now()
        wait_for_log(
            server_log_path,
            "The match is over",
            time.monotonic() + args.timelimit * 60 + 90,
            server,
        )
        result["matchEndedAt"] = utc_now()
        send_console(server, "sv_demostop", 2.0)
        demo = copy_latest_demo(serverdir, args.port, session_dir, started)
        digest = hashlib.sha256(demo.read_bytes()).hexdigest()
        result.update({"ok": True, "demo": str(demo), "demoBytes": demo.stat().st_size, "demoSha256": digest})
        return_code = 0
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return_code = 1
    finally:
        if server is not None and server.poll() is None:
            try:
                send_console(server, "quit", 0.1)
                server.wait(timeout=5)
            except Exception:
                terminate_owned(server, server_receipt, kill_group=inner_groups)
        terminate_owned(client, client_receipt, kill_group=inner_groups)
        if server_log_handle is not None:
            server_log_handle.close()
        if client_log_handle is not None:
            client_log_handle.close()
        cfg_path.unlink(missing_ok=True)
        result["endedAt"] = utc_now()
        write_json(session_dir / "match-result.json", result)
        (session_dir / "process-receipts.json").unlink(missing_ok=True)
        try:
            assert_udp_port_free(args.port)
            assert_tcp_port_free(args.control_port)
            result["portsReleased"] = True
        except OSError as exc:
            result["portsReleased"] = False
            result["portReleaseError"] = str(exc)
            return_code = 1
        write_json(session_dir / "match-result.json", result)
    return return_code


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--serverdir", type=Path, required=True)
    parser.add_argument("--mvdsv", type=Path)
    parser.add_argument("--rtx-client", type=Path, required=True)
    parser.add_argument("--qw-min-client", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--control-port", type=int, required=True)
    parser.add_argument("--timelimit", type=int, default=1)
    parser.add_argument("--bhop", type=int, choices=(0, 1), default=0)
    parser.add_argument("--inherit-process-group", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run_match(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
