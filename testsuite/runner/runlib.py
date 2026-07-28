"""Shared configuration, run-envelope, locking, and restoration utilities."""
from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from typing import Any
import tomllib

from . import __version__
from .checks import ValidationError, validate_result
from .control import Control


class ConfigError(ValueError):
    """Runner configuration is missing or invalid."""


class RunAborted(KeyboardInterrupt):
    """The run received SIGINT."""


def _strict_table(
    value: Any,
    path: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: expected table")
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required - optional)
    if missing:
        raise ConfigError(f"{path}: missing field(s): {', '.join(missing)}")
    if unknown:
        raise ConfigError(f"{path}: unknown field(s): {', '.join(unknown)}")
    return value


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    try:
        raw = config_path.read_bytes()
        config = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{config_path}: cannot read configuration: {exc}") from exc
    root = _strict_table(
        config,
        str(config_path),
        {"schema", "server", "paths", "build", "t2", "t3", "t4", "tools", "restore",
         # [sweep] drives several builds through the same tiers; its own
         # module validates the contents, this only admits the section.
         "sweep"},
    )
    restore = config.get("restore", {})
    if not isinstance(restore, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in restore.items()
    ):
        raise ConfigError(f"{config_path}.restore: expected a table of string cvar values")
    if root["schema"] != "rtx-testflow-config/1":
        raise ConfigError(
            f"{config_path}.schema: unsupported schema {root['schema']!r}"
        )
    server = _strict_table(
        root["server"],
        f"{config_path}.server",
        {"host", "control_port", "protocol"},
        optional={"demo_dir"},
    )
    if not isinstance(server["host"], str) or not server["host"]:
        raise ConfigError(f"{config_path}.server.host: expected non-empty string")
    if "demo_dir" in server and not isinstance(server["demo_dir"], str):
        raise ConfigError(f"{config_path}.server.demo_dir: expected string")
    if (
        isinstance(server["control_port"], bool)
        or not isinstance(server["control_port"], int)
        or not 1 <= server["control_port"] <= 65535
    ):
        raise ConfigError(f"{config_path}.server.control_port: invalid port")
    if server["protocol"] not in {"auto", "msgpack", "text"}:
        raise ConfigError(f"{config_path}.server.protocol: invalid protocol")
    _strict_table(
        root["paths"], f"{config_path}.paths", {"evidence_dir", "demos_dir"}
    )
    _strict_table(root["build"], f"{config_path}.build", {"repo_dir", "engine_binary"})
    t2 = _strict_table(root["t2"], f"{config_path}.t2", {"duration_s"})
    t3 = _strict_table(
        root["t3"],
        f"{config_path}.t3",
        {
            "duration_s",
            "reference_client",
            "branch_client",
            "seats_per_side",
            "match_server",
            "basedir",
            "control_port_base",
            "reference_branch",
            "reference_commit",
            "demoinfo_dir",
        },
        optional={"rig_up_cmd", "rig_down_cmd", "rig_boot_wait_s"},
    )
    t4 = _strict_table(
        root["t4"],
        f"{config_path}.t4",
        {"duration_s", "skills", "frogbot_server", "control_port", "demoinfo_dir"},
        optional={"rig_up_cmd", "rig_down_cmd", "rig_boot_wait_s"},
    )
    _strict_table(
        root["tools"],
        f"{config_path}.tools",
        {"qw_analyze"},
        optional={"mvd_api", "mvd_cache_dir"},
    )
    for table_name, table, key in (
        ("t2", t2, "duration_s"),
        ("t3", t3, "duration_s"),
        ("t3", t3, "seats_per_side"),
        ("t3", t3, "control_port_base"),
        ("t4", t4, "duration_s"),
        ("t4", t4, "control_port"),
    ):
        if (
            isinstance(table[key], bool)
            or not isinstance(table[key], int)
            or table[key] <= 0
        ):
            raise ConfigError(f"{config_path}.{table_name}.{key}: expected positive integer")
    if t4["skills"] != [10, 12, 14, 16, 18, 20]:
        raise ConfigError(
            f"{config_path}.t4.skills: expected [10, 12, 14, 16, 18, 20]"
        )
    for table_name, table in (
        ("paths", root["paths"]),
        ("build", root["build"]),
        ("t3", t3),
        ("t4", t4),
        ("tools", root["tools"]),
    ):
        for key, value in table.items():
            if key in {"duration_s", "seats_per_side", "skills", "control_port_base", "control_port"}:
                continue
            if key == "rig_boot_wait_s":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                    raise ConfigError(
                        f"{config_path}.{table_name}.{key}: expected non-negative number"
                    )
                continue
            if not isinstance(value, str):
                raise ConfigError(
                    f"{config_path}.{table_name}.{key}: expected string"
                )
    root["_meta"] = {
        "path": config_path.resolve(),
        "base": config_path.resolve().parent,
        "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }
    return root


def config_path(config: dict[str, Any], value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else config["_meta"]["base"] / path


def connect(config: dict[str, Any], timeout: float = 30.0) -> Control:
    server = config["server"]
    return Control(
        server["host"],
        server["control_port"],
        timeout=timeout,
        protocol=server["protocol"],
    )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ConfigError(f"{repo}: git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def _server_digest(status: Any) -> str | None:
    if not isinstance(status, dict):
        return None
    for key in ("digest_md5", "build_md5", "digest"):
        value = status.get(key)
        if isinstance(value, str) and value:
            return value
    build = status.get("build")
    if isinstance(build, dict):
        return _server_digest(build)
    return None


def _engine_digest(config: dict[str, Any]) -> str | None:
    """md5 (8 hex chars, display id) of the deployed engine binary, when the
    config names one. This binds the evidence to the binary actually running,
    which the repo checkout identity alone cannot do."""
    binary = config.get("build", {}).get("engine_binary", "")
    if not binary:
        return None
    path = config_path(config, binary)
    if not path.is_file():
        raise ConfigError(f"build.engine_binary: {path} does not exist")
    import hashlib

    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:8]


def build_identity(
    config: dict[str, Any], server_status: dict[str, Any] | None = None
) -> dict[str, Any]:
    repo = config_path(config, config["build"]["repo_dir"]).resolve()
    commit = _git(repo, "rev-parse", "HEAD")
    if len(commit) != 40:
        raise ConfigError(f"{repo}: git returned a non-full commit hash")
    branch = _git(repo, "branch", "--show-current") or "detached"
    dirty = bool(_git(repo, "status", "--porcelain"))
    return {
        "branch": branch,
        "commit": commit,
        "digest_md5": _server_digest(server_status) or _engine_digest(config),
        "dirty": dirty,
    }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_write_json(path: str | Path, document: Any) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


class RigLifecycle(AbstractContextManager["RigLifecycle"]):
    """Optional on-demand rig control.

    When the tier's config table carries ``rig_up_cmd``, it is executed (shell)
    before the run and must succeed; ``rig_boot_wait_s`` (default 5) then gives
    the rig time to boot before the preflight probes it. ``rig_down_cmd`` runs
    best-effort on the way out — including on failure and abort — so an
    on-demand rig never stays up between runs. Both commands empty or absent
    means the operator manages the rig, exactly as before.
    """

    def __init__(self, section: dict[str, Any]):
        self.up = str(section.get("rig_up_cmd", "") or "")
        self.down = str(section.get("rig_down_cmd", "") or "")
        wait = section.get("rig_boot_wait_s", 5)
        self.boot_wait = float(wait if isinstance(wait, (int, float)) else 5)

    def __enter__(self) -> "RigLifecycle":
        if self.up:
            subprocess.run(self.up, shell=True, check=True)
            if self.boot_wait:
                time.sleep(self.boot_wait)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self.down:
            subprocess.run(self.down, shell=True, check=False)


class RigLock(AbstractContextManager["RigLock"]):
    """Exclusive local lock for one configured control port."""

    def __init__(self, port: int):
        self.path = Path(tempfile.gettempdir()) / f"rtx-testflow-control-{port}.lock"
        self.acquired = False

    def __enter__(self) -> "RigLock":
        for attempt in range(2):
            try:
                descriptor = os.open(
                    self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
                with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                    stream.write(f"{os.getpid()}\n")
                self.acquired = True
                return self
            except FileExistsError:
                try:
                    owner = int(self.path.read_text(encoding="ascii").strip())
                    os.kill(owner, 0)
                except (OSError, ValueError):
                    if attempt == 0:
                        self.path.unlink(missing_ok=True)
                        continue
                raise RuntimeError(
                    f"control port is locked by another runner: {self.path}"
                )
        raise RuntimeError(f"cannot acquire rig lock: {self.path}")

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False


def _cvar_values(status: Any) -> dict[str, str]:
    if not isinstance(status, dict):
        return {}
    raw = status.get("cvars")
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    if isinstance(raw, list):
        output = {}
        for item in raw:
            if isinstance(item, dict) and "name" in item and "value" in item:
                output[str(item["name"])] = str(item["value"])
        return output
    return {}


class CvarRestore(AbstractContextManager["CvarRestore"]):
    """Restore named cvars on every exit, and record who vouched for each.

    Preferred source is a live snapshot from server status; engines that do not
    expose cvars in status (the current control protocol does not) fall back to
    the config-declared `[restore]` baseline — the rig's documented idle state.
    A cvar available from neither source is a hard preflight error.

    The baseline fallback is right for *restoring* a value and wrong as evidence
    that the build under test has the cvar at all: our own configuration would
    then vouch for a capability the binary does not have, and a tier that asked
    "can this build measure X" would get yes. So the two questions are kept
    apart — `restore_source` says what will be put back, `server_has` says
    whether the server itself answered."""

    def __init__(self, control: Control, names: list[str], baseline: dict[str, str] | None = None):
        self.control = control
        self.names = names
        self.baseline = {str(k): str(v) for k, v in (baseline or {}).items()}
        self.snapshot: dict[str, str] = {}
        self.sources: dict[str, str] = {}

    def restore_source(self, name: str) -> str | None:
        """Where the value that will be restored came from."""
        return self.sources.get(name)

    def server_has(self, name: str) -> bool:
        """Did the server itself answer for this cvar.

        This is the capability probe: a build without the cvar cannot produce
        the signal behind it, whatever our config says about the idle value.
        """
        return self.sources.get(name) in {"status", "get"}

    def __enter__(self) -> "CvarRestore":
        status = self.control.request("status")["data"]
        values = _cvar_values(status)
        missing = []
        for name in self.names:
            if name in values:
                self.snapshot[name] = values[name]
                self.sources[name] = "status"
                continue
            # Two attempts before concluding the build lacks the cvar: one
            # dropped reply would otherwise be recorded as a missing capability,
            # which is a lie in the more damaging direction — it would mark a
            # build that measures fine as unable to measure.
            for _ in range(2):
                try:
                    reply = self.control.request(f"get {name}", timeout=8.0)["data"]
                    self.snapshot[name] = str(reply["string"])
                    self.sources[name] = "get"
                    break
                except Exception:
                    continue
            if name in self.sources:
                continue
            if name in self.baseline:
                self.snapshot[name] = self.baseline[name]
                self.sources[name] = "baseline"
            else:
                missing.append(name)
        if missing:
            raise RuntimeError(
                "no restore value for cvar(s) "
                + ", ".join(missing)
                + ": neither status, the Get verb, nor the config [restore]"
                " baseline provided one — declare the rig's idle values under"
                " [restore] in config.toml"
            )
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        errors = []
        for name, value in self.snapshot.items():
            try:
                self.control.request(f"set {name} {value}", timeout=8.0)
            except Exception as restore_error:
                errors.append(f"{name}: {restore_error}")
        if errors and exc is None:
            raise RuntimeError("failed to restore cvars: " + "; ".join(errors))


class RunRecorder(AbstractContextManager["RunRecorder"]):
    """Build and atomically persist one result envelope on every exit."""

    def __init__(
        self,
        config: dict[str, Any],
        tier: str,
        map_name: str,
        *,
        provenance: str = "measured",
        server_status: dict[str, Any] | None = None,
    ):
        self.config = config
        self.tier = tier
        self.map_name = map_name
        self.provenance = provenance
        self.started = utc_now()
        self.build = build_identity(config, server_status)
        stamp = self.started.strftime("%Y%m%dT%H%M%SZ")
        self.run_id = f"{tier.lower()}-{stamp}-{self.build['commit'][:8]}"
        self.payload: dict[str, Any] = {}
        # What the build under test could not be asked about. A property of the
        # binary and the rig rather than of one tier's numbers, so it sits
        # beside the payload rather than inside it. Absent means everything the
        # tier needed was available, which is the common case.
        self.capabilities: dict[str, Any] | None = None
        self.status = "complete"
        self.error: str | None = None
        self.path = (
            config_path(config, config["paths"]["evidence_dir"])
            / f"{self.run_id}.json"
        )
        if self.path.exists():
            raise RuntimeError(
                f"run id collision at {self.path}; retry after the UTC clock advances"
            )
        self._previous_sigint: Any = None

    def _sigint(self, signum, frame) -> None:
        self.status = "aborted"
        self.error = "interrupted by SIGINT"
        raise RunAborted(self.error)

    def __enter__(self) -> "RunRecorder":
        self._previous_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._sigint)
        return self

    def document(self) -> dict[str, Any]:
        document = {
            "schema": "rtx-testflow/1",
            "run_id": self.run_id,
            "tier": self.tier,
            "status": self.status,
            "started_utc": utc_text(self.started),
            "ended_utc": utc_text(utc_now()),
            "map": self.map_name,
            "build": self.build,
            "config_digest": self.config["_meta"]["digest"],
            "runner_version": __version__,
            "provenance": self.provenance,
            "payload": self.payload,
        }
        if self.capabilities is not None:
            document["capabilities"] = self.capabilities
        if self.error is not None:
            document["error"] = self.error
        return document

    def __exit__(self, exc_type, exc, traceback) -> bool:
        signal.signal(signal.SIGINT, self._previous_sigint)
        if exc is not None:
            if isinstance(exc, (RunAborted, KeyboardInterrupt, TimeoutError)):
                self.status = "aborted"
            else:
                self.status = "failed"
            self.error = str(exc) or exc.__class__.__name__
        document = self.document()
        validation_error: ValidationError | None = None
        try:
            validate_result(document, str(self.path))
        except ValidationError as error:
            if self.status != "complete":
                raise
            validation_error = error
            self.status = "failed"
            self.error = str(error)
            document = self.document()
            validate_result(document, str(self.path))
        atomic_write_json(self.path, document)
        if validation_error is not None:
            raise validation_error
        return False
