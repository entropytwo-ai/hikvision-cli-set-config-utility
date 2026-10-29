"""Small helpers shared by the feature modules (argparse glue, value parsing)."""
from __future__ import annotations

import argparse
from typing import Any, Callable, List, Optional, Tuple


def add_command(subparsers, name: str, help_text: str, handler: Callable,
                parents: List[argparse.ArgumentParser], epilog: Optional[str] = None,
                writes: bool = False, change_opts: Optional[List[str]] = None,
                ) -> argparse.ArgumentParser:
    """Register a subcommand whose handler is called once per camera.

    change_opts: argument dests of which at least one must be given (checked by
    hik_config before any camera is contacted), so "set-x" with no options fails fast.
    """
    parser = subparsers.add_parser(
        name, help=help_text, description=help_text, parents=parents, epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.set_defaults(handler=handler, command=name, writes=writes,
                        change_opts=change_opts or [])
    return parser


def on_off(value: str) -> bool:
    """argparse type accepting on/off, true/false, yes/no, enable/disable, 1/0."""
    v = str(value).strip().lower()
    if v in ("on", "true", "yes", "1", "enable", "enabled"):
        return True
    if v in ("off", "false", "no", "0", "disable", "disabled"):
        return False
    raise argparse.ArgumentTypeError(f"expected on/off, got {value!r}")


def int_range(lo: int, hi: int) -> Callable[[str], int]:
    def _parse(value: str) -> int:
        try:
            n = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}")
        if not lo <= n <= hi:
            raise argparse.ArgumentTypeError(f"must be between {lo} and {hi}")
        return n
    return _parse


def position(value: str) -> Tuple[int, int]:
    """Parse 'X,Y' into a tuple of ints (OSD coordinates)."""
    try:
        x, y = (int(p) for p in value.split(","))
        return x, y
    except Exception:
        raise argparse.ArgumentTypeError(f"expected X,Y (e.g. 16,32), got {value!r}")


def resolution(value: str) -> Tuple[int, int]:
    try:
        w, h = (int(p) for p in value.lower().replace("*", "x").split("x"))
        return w, h
    except Exception:
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT (e.g. 1920x1080), got {value!r}")


def channel_of(args: Any, camera: Any) -> int:
    """--channel on the command line wins over the camera file's 'channel' field."""
    value = getattr(args, "channel", None)
    return int(value) if value else int(getattr(camera, "channel", 1) or 1)


def yes_no(value: Optional[bool]) -> str:
    if value is None:
        return "unknown"
    return "enabled" if value else "disabled"
