"""Time, timezone and DST (ISAPI /ISAPI/System/time).

Hikvision stores the timezone *and* the DST rule in one POSIX-style string, e.g.

    EST5:00:00EDT,M3.2.0/02:00:00,M11.1.0/02:00:00
    CST-8:00:00                     (UTC+8, no DST - note the inverted sign)

Writes use the minimal "GUI-style" XML payload that the web UI sends, because
many older firmwares reject full-schema payloads (see the original
set-hik-ntp.py notes):

    <Time><timeMode>manual</timeMode><localTime>...</localTime><timeZone>...</timeZone></Time>
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional

from .camera_client import CameraClient, text, xml_escape
from .common import add_command, on_off

TIME_PATH = "/ISAPI/System/time"

# Best-effort presets in Hikvision's POSIX notation. Use --timezone for anything else.
TZ_PRESETS = {
    "UTC": "UTC0:00:00",
    "US-Eastern": "EST5:00:00EDT,M3.2.0/02:00:00,M11.1.0/02:00:00",
    "US-Central": "CST6:00:00CDT,M3.2.0/02:00:00,M11.1.0/02:00:00",
    "US-Mountain": "MST7:00:00MDT,M3.2.0/02:00:00,M11.1.0/02:00:00",
    "US-Arizona": "MST7:00:00",
    "US-Pacific": "PST8:00:00PDT,M3.2.0/02:00:00,M11.1.0/02:00:00",
    "US-Alaska": "AKST9:00:00AKDT,M3.2.0/02:00:00,M11.1.0/02:00:00",
    "US-Hawaii": "HST10:00:00",
    "UK": "GMT0:00:00BST,M3.5.0/01:00:00,M10.5.0/02:00:00",
    "Europe-Central": "CET-1:00:00CEST,M3.5.0/02:00:00,M10.5.0/03:00:00",
    "Europe-Eastern": "EET-2:00:00EEST,M3.5.0/03:00:00,M10.5.0/04:00:00",
    "India": "IST-5:30:00",
    "China": "CST-8:00:00",
    "Japan": "JST-9:00:00",
    "Australia-Sydney": "AEST-10:00:00AEDT,M10.1.0/02:00:00,M4.1.0/03:00:00",
}

DEFAULT_DST_START = "M3.2.0/02:00:00"
DEFAULT_DST_END = "M11.1.0/02:00:00"

_TZ_RE = re.compile(
    r"^(?P<std>[A-Za-z]+|<[^>]+>)(?P<offset>[+-]?\d{1,2}(?::\d{2}){0,2})"
    r"(?P<dst>[A-Za-z]+|<[^>]+>)?(?P<dstoffset>[+-]?\d{1,2}(?::\d{2}){0,2})?$"
)


def parse_timezone(tz: Optional[str]) -> Dict[str, Any]:
    """Split a Hikvision timezone string into its parts (best effort)."""
    info: Dict[str, Any] = {"raw": tz, "std_name": None, "posix_offset": None, "utc_offset": None,
                            "dst_enabled": False, "dst_name": None, "dst_offset": None,
                            "dst_start": None, "dst_end": None}
    if not tz:
        return info
    base, *rules = tz.split(",")
    m = _TZ_RE.match(base.strip())
    if m:
        info["std_name"] = m.group("std")
        info["posix_offset"] = m.group("offset")
        info["utc_offset"] = _posix_to_utc(m.group("offset"))
        if m.group("dst"):
            info["dst_enabled"] = True
            info["dst_name"] = m.group("dst")
            info["dst_offset"] = m.group("dstoffset")
    if len(rules) >= 2:
        info["dst_enabled"] = True
        info["dst_start"], info["dst_end"] = rules[0], rules[1]
    return info


def _posix_to_utc(offset: str) -> str:
    """POSIX offsets are inverted: 'EST5' means UTC-05:00."""
    sign = "-" if not offset.startswith("-") else "+"
    parts = offset.lstrip("+-").split(":") + ["0", "0"]
    return f"UTC{sign}{int(parts[0]):02d}:{int(parts[1]):02d}"


def build_timezone(current: str, dst: Optional[bool] = None, dst_name: Optional[str] = None,
                   dst_start: Optional[str] = None, dst_end: Optional[str] = None) -> str:
    """Return ``current`` with DST switched on/off or its rule replaced."""
    info = parse_timezone(current)
    if not info["std_name"]:
        raise ValueError(f"cannot parse current timezone {current!r}; pass --timezone explicitly")
    base = f"{info['std_name']}{info['posix_offset']}"
    enable = info["dst_enabled"] if dst is None else dst
    if not enable:
        return base
    name = dst_name or info["dst_name"] or "DST"
    start = dst_start or info["dst_start"] or DEFAULT_DST_START
    end = dst_end or info["dst_end"] or DEFAULT_DST_END
    return f"{base}{name}{info['dst_offset'] or ''},{start},{end}"


class TimeDate:
    def __init__(self, client: CameraClient):
        self.client = client

    def get_raw(self) -> Dict[str, Optional[str]]:
        root = self.client.get_xml(TIME_PATH)
        return {"timeMode": text(root, "timeMode"), "localTime": text(root, "localTime"),
                "timeZone": text(root, "timeZone")}

    def get(self) -> Dict[str, Any]:
        raw = self.get_raw()
        tz = parse_timezone(raw["timeZone"])
        result = {
            "time_mode": raw["timeMode"],
            "device_time": raw["localTime"],
            "timezone": raw["timeZone"],
            "utc_offset": tz["utc_offset"],
            "dst": "enabled" if tz["dst_enabled"] else "disabled",
        }
        if tz["dst_enabled"]:
            result["dst_start"] = tz["dst_start"]
            result["dst_end"] = tz["dst_end"]
        return result

    def put_minimal(self, time_mode: str, timezone: str, local_time: Optional[str] = None) -> None:
        """PUT the GUI-style minimal <Time> payload (most firmware-compatible form)."""
        body = ['<?xml version="1.0" encoding="UTF-8"?>', "<Time>",
                f"  <timeMode>{xml_escape(time_mode)}</timeMode>"]
        if local_time:
            body.append(f"  <localTime>{xml_escape(local_time)}</localTime>")
        body.append(f"  <timeZone>{xml_escape(timezone)}</timeZone>")
        body.append("</Time>")
        self.client.put_xml(TIME_PATH, "\n".join(body))


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def cmd_get_time(client, log, args, camera) -> None:
    with log.step("retrieve time settings"):
        info = TimeDate(client).get()
        log.ok("Time settings retrieved successfully")
        log.data("Time", info, "time")


def cmd_set_time(client, log, args, camera) -> None:
    td = TimeDate(client)
    with log.step("read current time settings") as s:
        current = td.get_raw()
    if not s.ok:
        return

    with log.step("build new time settings") as s:
        timezone = current["timeZone"] or ""
        if args.tz_preset:
            timezone = TZ_PRESETS[args.tz_preset]
        if args.timezone:
            timezone = args.timezone
        if args.dst is not None or args.dst_start or args.dst_end or args.dst_name:
            timezone = build_timezone(timezone, args.dst, args.dst_name, args.dst_start, args.dst_end)
        if not timezone:
            raise ValueError("camera returned no timezone; pass --timezone")

        mode = current["timeMode"] or "manual"
        local_time = None
        if args.mode:
            mode = "NTP" if args.mode == "ntp" else "manual"
        if args.sync_to_host or args.manual_time:
            mode = "manual"
            if args.sync_to_host:
                local_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            else:
                local_time = datetime.strptime(args.manual_time.replace(" ", "T"),
                                               "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%dT%H:%M:%S")
    if not s.ok:
        return

    with log.step("set time settings"):
        td.put_minimal(mode, timezone, local_time)
        log.ok(f"Time mode set to: {mode}")
        if timezone != current["timeZone"]:
            log.ok(f"Timezone set to: {timezone} ({parse_timezone(timezone)['utc_offset']}, "
                   f"DST {'on' if parse_timezone(timezone)['dst_enabled'] else 'off'})")
        if local_time:
            log.ok(f"Device clock set to: {local_time}")


def register(sub, parents) -> None:
    add_command(sub, "get-time", "Read device time, time mode, timezone and DST settings",
                cmd_get_time, parents)
    p = add_command(sub, "set-time", "Set manual time, timezone and DST", cmd_set_time, parents,
                    writes=True, change_opts=["mode", "manual_time", "sync_to_host", "timezone",
                                              "tz_preset", "dst", "dst_name", "dst_start", "dst_end"],
                    epilog=(
                        "Timezone presets: " + ", ".join(TZ_PRESETS) + "\n"
                        "Hikvision timezone strings use POSIX notation (sign inverted):\n"
                        "  EST5:00:00EDT,M3.2.0/02:00:00,M11.1.0/02:00:00 = US Eastern with DST"))
    p.add_argument("--mode", choices=["manual", "ntp"], help="time synchronisation mode")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--manual-time", metavar="YYYY-MM-DDTHH:MM:SS",
                   help="set the device clock to this local time (implies --mode manual)")
    g.add_argument("--sync-to-host", action="store_true",
                   help="set the device clock to this computer's local time (implies --mode manual)")
    tz = p.add_mutually_exclusive_group()
    tz.add_argument("--timezone", metavar="TZ", help="raw Hikvision/POSIX timezone string")
    tz.add_argument("--tz-preset", choices=sorted(TZ_PRESETS), help="named timezone preset")
    p.add_argument("--dst", type=on_off, metavar="on|off", help="enable or disable daylight saving time")
    p.add_argument("--dst-name", help="DST abbreviation when enabling DST (e.g. EDT)")
    p.add_argument("--dst-start", metavar="RULE", help=f"DST start rule (default {DEFAULT_DST_START})")
    p.add_argument("--dst-end", metavar="RULE", help=f"DST end rule (default {DEFAULT_DST_END})")
