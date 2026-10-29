"""Concurrent runner with per-camera, human-readable logging.

Each camera gets its own ``CameraLog``. Messages are buffered while the camera
is being processed and printed as one contiguous block when it finishes, so
output from parallel threads never interleaves. The same block is appended
(with timestamps) to the log file.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .camera_client import CameraClient, ISAPIError
from .camera_loader import Camera

try:
    from colorama import Fore, Style, init as _colorama_init
    _colorama_init()
    _HAVE_COLOR = True
except ImportError:  # pragma: no cover
    _HAVE_COLOR = False

RULE = "─" * 60
OK, FAIL, INFO, WARN, DATA = "ok", "fail", "info", "warn", "data"
SYMBOLS = {OK: "✓", FAIL: "✗", INFO: "•", WARN: "!", DATA: " "}


class _Colors:
    enabled = _HAVE_COLOR

    @classmethod
    def paint(cls, text: str, kind: str) -> str:
        if not cls.enabled:
            return text
        color = {OK: Fore.GREEN, FAIL: Fore.RED, WARN: Fore.YELLOW, INFO: Fore.CYAN,
                 "head": Style.BRIGHT, DATA: ""}.get(kind, "")
        return f"{color}{text}{Style.RESET_ALL}" if color else text


def set_color(enabled: bool) -> None:
    _Colors.enabled = enabled and _HAVE_COLOR


# ─────────────────────────────────────────────────────────────────────────────
# Per-camera log
# ─────────────────────────────────────────────────────────────────────────────

class _Step:
    def __init__(self, log: "CameraLog", description: str):
        self.log = log
        self.description = description
        self.ok = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            return False
        if issubclass(exc_type, (ISAPIError, ValueError, KeyError)):
            self.ok = False
            reason = str(exc) if not issubclass(exc_type, KeyError) else f"missing field {exc}"
            self.log.fail(f"Failed to {self.description}: {reason}")
            return True  # swallow - camera processing continues
        return False


class CameraLog:
    """Collects human-readable lines and structured results for one camera."""

    def __init__(self, camera: Camera, dry_run: bool = False):
        self.camera = camera
        self.dry_run = dry_run
        self.entries: List[tuple] = []  # (timestamp, kind, text)
        self.results: Dict[str, Any] = {}
        self.ok_count = 0
        self.fail_count = 0

    def _add(self, kind: str, msg: str) -> None:
        self.entries.append((datetime.now(), kind, msg))

    def ok(self, msg: str) -> None:
        self.ok_count += 1
        self._add(OK, f"[DRY-RUN] {msg}" if self.dry_run else msg)

    def fail(self, msg: str) -> None:
        self.fail_count += 1
        self._add(FAIL, msg)

    def info(self, msg: str) -> None:
        self._add(INFO, msg)

    def warn(self, msg: str) -> None:
        self._add(WARN, msg)

    def data(self, title: str, values: Any, record_key: Optional[str] = None) -> None:
        """Pretty-print a dict/list of values under a title and store it for --json-out."""
        self._add(DATA, f"{title}:")
        for line in _format_value(values, indent=2):
            self._add(DATA, line)
        self.results[record_key or title] = values

    def step(self, description: str) -> _Step:
        """Context manager: ISAPI/validation errors inside become a '✗ Failed to …' line."""
        return _Step(self, description)

    @property
    def success(self) -> bool:
        return self.fail_count == 0

    def render(self, color: bool = True) -> str:
        head = f"Camera: {self.camera.label} ({self.camera.username or '?'})"
        lines = [RULE, _Colors.paint(head, "head") if color else head, RULE]
        if not self.entries:
            lines.append("  (no operations performed)")
        for _, kind, msg in self.entries:
            prefix = "    " if kind == DATA else f"  {SYMBOLS[kind]} "
            line = prefix + msg
            lines.append(_Colors.paint(line, kind) if color and kind != DATA else line)
        lines.append(RULE)
        return "\n".join(lines)

    def render_logfile(self) -> str:
        tag = self.camera.label
        out = []
        for ts, kind, msg in self.entries:
            symbol = SYMBOLS[kind] if kind != DATA else " "
            out.append(f"{ts:%Y-%m-%d %H:%M:%S} [{tag}] {symbol} {msg}")
        return "\n".join(out)


def _format_value(value: Any, indent: int) -> List[str]:
    pad = " " * indent
    lines: List[str] = []
    if isinstance(value, dict):
        width = max((len(str(k)) for k in value), default=0)
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.extend(_format_value(v, indent + 2))
            else:
                shown = "" if v is None or v == {} or v == [] else v
                lines.append(f"{pad}{str(k).ljust(width)} : {shown}")
    elif isinstance(value, list):
        for i, item in enumerate(value, 1):
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}- [{i}]")
                lines.extend(_format_value(item, indent + 4))
            else:
                lines.append(f"{pad}- {item}")
        if not value:
            lines.append(f"{pad}(none)")
    else:
        lines.append(f"{pad}{value}")
    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

Handler = Callable[[CameraClient, CameraLog, Any], None]


@dataclass
class RunOptions:
    workers: int = 8
    timeout: float = 5.0
    auth_mode: str = "auto"
    use_https: bool = False
    verify_tls: bool = False
    dry_run: bool = False
    log_file: Optional[str] = "hik-config.log"
    json_out: Optional[str] = None
    color: bool = True
    quiet: bool = False
    command: str = ""


@dataclass
class RunSummary:
    logs: List[CameraLog] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def successes(self) -> int:
        return sum(1 for log in self.logs if log.success)

    @property
    def failures(self) -> int:
        return len(self.logs) - self.successes


class Runner:
    def __init__(self, options: RunOptions):
        self.options = options
        self._print_lock = threading.Lock()
        set_color(options.color)

    def _process(self, camera: Camera, handler: Handler, args: Any) -> CameraLog:
        log = CameraLog(camera, dry_run=self.options.dry_run)
        client = CameraClient(
            camera.ip, camera.username or "", camera.password or "",
            port=camera.effective_port, use_https=camera.https or self.options.use_https,
            timeout=self.options.timeout, auth_mode=camera.auth or self.options.auth_mode,
            verify_tls=self.options.verify_tls, dry_run=self.options.dry_run,
        )
        try:
            handler(client, log, args, camera)
        except Exception as exc:  # never let one camera crash the run
            log.fail(f"Unexpected error: {exc.__class__.__name__}: {exc}")
            log.results["traceback"] = traceback.format_exc()
        finally:
            client.close()
        self._emit(log)
        return log

    def _emit(self, log: CameraLog) -> None:
        with self._print_lock:
            if not self.options.quiet or not log.success:
                print(log.render(color=self.options.color), flush=True)
            if self.options.log_file:
                with open(self.options.log_file, "a", encoding="utf-8") as fh:
                    fh.write(log.render_logfile() + "\n")

    def run(self, cameras: List[Camera], handler: Handler, args: Any) -> RunSummary:
        start = time.monotonic()
        self._log_line(f"=== hik-config '{self.options.command}' started on "
                       f"{len(cameras)} camera(s){' [DRY-RUN]' if self.options.dry_run else ''} ===")
        summary = RunSummary()
        workers = max(1, min(self.options.workers, len(cameras)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hik") as pool:
            futures = {pool.submit(self._process, cam, handler, args): cam for cam in cameras}
            for future in as_completed(futures):
                summary.logs.append(future.result())
        # Keep the summary in the same order as the input file.
        order = {id(cam): i for i, cam in enumerate(cameras)}
        summary.logs.sort(key=lambda lg: order.get(id(lg.camera), 0))
        summary.elapsed = time.monotonic() - start
        self._print_summary(summary)
        self._log_line(f"=== hik-config '{self.options.command}' finished: "
                       f"{summary.successes} ok, {summary.failures} failed ===")
        if self.options.json_out:
            self._write_json(summary)
        return summary

    def _log_line(self, msg: str) -> None:
        if self.options.log_file:
            with open(self.options.log_file, "a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")

    def _print_summary(self, summary: RunSummary) -> None:
        c = self.options.color
        paint = (lambda t, k: _Colors.paint(t, k)) if c else (lambda t, k: t)
        print()
        print("═" * 60)
        print(paint(f"SUMMARY  ({self.options.command}"
                    f"{', dry-run' if self.options.dry_run else ''})", "head"))
        print("═" * 60)
        width = max((len(lg.camera.label) for lg in summary.logs), default=10)
        for lg in summary.logs:
            status = paint("✓ OK    ", OK) if lg.success else paint("✗ FAILED", FAIL)
            print(f"  {status}  {lg.camera.label.ljust(width)}   "
                  f"{lg.ok_count} succeeded, {lg.fail_count} failed")
        print("─" * 60)
        print(f"  Total cameras : {len(summary.logs)}")
        print(paint(f"  Successful    : {summary.successes}", OK))
        print(paint(f"  Failed        : {summary.failures}", FAIL if summary.failures else OK))
        print(f"  Elapsed       : {summary.elapsed:.1f}s")
        if self.options.log_file:
            print(f"  Log file      : {self.options.log_file}")
        if self.options.json_out:
            print(f"  JSON results  : {self.options.json_out}")
        print("═" * 60)

    def _write_json(self, summary: RunSummary) -> None:
        payload = {
            "command": self.options.command,
            "dry_run": self.options.dry_run,
            "generated": datetime.now().isoformat(timespec="seconds"),
            "total": len(summary.logs),
            "successes": summary.successes,
            "failures": summary.failures,
            "cameras": [
                {
                    "ip": lg.camera.ip,
                    "port": lg.camera.effective_port,
                    "name": lg.camera.name,
                    "success": lg.success,
                    "messages": [{"time": ts.isoformat(timespec="seconds"), "level": kind, "text": msg}
                                 for ts, kind, msg in lg.entries if kind != DATA],
                    "results": lg.results,
                }
                for lg in summary.logs
            ],
        }
        with open(self.options.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
