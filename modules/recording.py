"""On-camera recording schedule (ISAPI /ISAPI/ContentMgmt/record/tracks/<id>).

Track ids follow the stream ids: 101 = channel 1 main stream, 102 = sub stream.
Applies to cameras recording to their own SD card / NAS. (For NVR recording,
run the same commands against the NVR.)
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .camera_client import CameraClient, add_children, find, find_any, set_text, text
from .common import add_command, channel_of, int_range, on_off

TRACK_PATH = "/ISAPI/ContentMgmt/record/tracks/{id}"
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MODES = {  # CLI name -> ActionRecordingMode
    "continuous": "CMR",
    "motion": "MOTION",
    "alarm": "ALARM",
    "motion-or-alarm": "EDR",
    "motion-and-alarm": "ALARMANDMOTION",
    "event": "AllEvent",
}
MODE_NAMES = {v.upper(): k for k, v in MODES.items()}


def parse_days(spec: str) -> List[str]:
    spec = spec.strip().lower()
    if spec in ("all", "everyday", "daily"):
        return DAYS[:]
    if spec == "weekdays":
        return DAYS[:5]
    if spec == "weekends":
        return DAYS[5:]
    result = []
    for part in spec.split(","):
        match = [d for d in DAYS if d.lower().startswith(part.strip()[:3])]
        if not match or len(part.strip()) < 3:
            raise ValueError(f"unknown day {part!r} (use Mon,Tue,… or all/weekdays/weekends)")
        result.append(match[0])
    return result


def _time(value: str) -> str:
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"invalid time {value!r}, expected HH:MM")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 24 and 0 <= m < 60) or (h == 24 and m):
        raise ValueError(f"invalid time {value!r}")
    return f"{h:02d}:{m:02d}:00"


class Recording:
    def __init__(self, client: CameraClient, channel: int = 1, stream: str = "main"):
        self.client = client
        self.track_id = channel * 100 + (1 if stream == "main" else 2)
        self.path = TRACK_PATH.format(id=self.track_id)

    def get(self) -> Dict[str, Any]:
        root = self.client.get_xml(self.path)
        schedule = []
        for action in root.iter("ScheduleAction"):
            mode = text(action, "Actions/ActionRecordingMode") or ""
            schedule.append(
                f"{text(action, 'ScheduleActionStartTime/DayOfWeek')} "
                f"{(text(action, 'ScheduleActionStartTime/TimeOfDay') or '')[:5]}-"
                f"{(text(action, 'ScheduleActionEndTime/TimeOfDay') or '')[:5]} "
                f"{MODE_NAMES.get(mode.upper(), mode)}"
                + ("" if (text(action, "Actions/Record") or "true") == "true" else " (record off)"))

        def _any(tag):
            el = find_any(root, tag)
            return el.text.strip() if el is not None and el.text else None

        return {
            "track": self.track_id,
            "schedule_enabled": _any("enableSchedule"),
            "pre_record_sec": _any("PreRecordTimeSeconds"),
            "post_record_sec": _any("PostRecordTimeSeconds"),
            "overwrite_when_full": _any("LoopEnable"),
            "retention": _any("Duration"),
            "record_audio": _any("SaveAudio"),
            "schedule": schedule,
        }

    def set(self, enable: Optional[bool] = None, pre: Optional[int] = None, post: Optional[int] = None,
            mode: Optional[str] = None, days: Optional[List[str]] = None, start: str = "00:00",
            end: str = "24:00", audio: Optional[bool] = None) -> List[str]:
        root = self.client.get_xml(self.path)
        changes: List[str] = []
        ext_path = "CustomExtensionList/CustomExtension"

        def _put_ext(tag, value):
            el = find_any(root, tag)
            if el is not None:
                el.text = "true" if value is True else "false" if value is False else str(value)
            else:
                set_text(root, f"{ext_path}/{tag}", value)

        if enable is not None:
            _put_ext("enableSchedule", enable)
            changes.append(f"schedule {'enabled' if enable else 'disabled'}")
        if pre is not None:
            _put_ext("PreRecordTimeSeconds", pre)
            changes.append(f"pre-record {pre}s")
        if post is not None:
            _put_ext("PostRecordTimeSeconds", post)
            changes.append(f"post-record {post}s")
        if audio is not None:
            _put_ext("SaveAudio", audio)
            changes.append(f"audio {'on' if audio else 'off'}")
        if mode:
            self._replace_schedule(root, MODES[mode], days or DAYS, _time(start), _time(end))
            changes.append(f"schedule {mode} on {_days_label(days or DAYS)} "
                           f"{_time(start)[:5]}-{_time(end)[:5]}")
        self.client.put_xml(self.path, root)
        return changes

    @staticmethod
    def _replace_schedule(root: ET.Element, mode: str, days: List[str], start: str, end: str) -> None:
        if start >= end:
            raise ValueError("schedule start time must be before end time")
        block = find(root, "TrackSchedule/ScheduleBlock")
        if block is None:
            schedule = find(root, "TrackSchedule")
            if schedule is None:  # (Element truthiness depends on children - test for None)
                schedule = ET.SubElement(root, "TrackSchedule")
            block = ET.SubElement(schedule, "ScheduleBlock")
            add_children(block, {"ScheduleBlockGUID": "{00000000-0000-0000-0000-000000000000}",
                                 "ScheduleBlockType": "www.std-cgi.com/racm/schedule/ver10"})
        for action in block.findall("ScheduleAction"):
            block.remove(action)
        for i, day in enumerate(days, 1):
            action = ET.SubElement(block, "ScheduleAction")
            add_children(action, {
                "id": i,
                "ScheduleActionStartTime": {"DayOfWeek": day, "TimeOfDay": start},
                "ScheduleActionEndTime": {"DayOfWeek": day, "TimeOfDay": end},
                "ScheduleDSTEnable": False,
                "Description": "nothing",
                "Actions": {"Record": True, "Log": False, "SaveImg": False,
                            "ActionRecordingMode": mode},
            })


def _days_label(days: List[str]) -> str:
    if days == DAYS:
        return "all days"
    if days == DAYS[:5]:
        return "weekdays"
    return ",".join(d[:3] for d in days)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def cmd_get_recording(client, log, args, camera) -> None:
    with log.step(f"retrieve recording config ({args.stream} stream)"):
        info = Recording(client, channel_of(args, camera), args.stream).get()
        log.ok("Recording config retrieved successfully")
        log.data("Recording", info, "recording")


def cmd_set_recording(client, log, args, camera) -> None:
    with log.step("set recording config"):
        days = parse_days(args.days) if args.days else None
        changes = Recording(client, channel_of(args, camera), args.stream).set(
            args.enable, args.pre_record, args.post_record, args.mode, days, args.start, args.end,
            args.audio)
        log.ok("Recording set to: " + ", ".join(changes))


def register(sub, parents) -> None:
    p = add_command(sub, "get-recording", "Read recording schedule and pre/post-record times",
                    cmd_get_recording, parents)
    p.add_argument("--stream", choices=["main", "sub"], default="main", help="recording track")
    p.add_argument("--channel", type=int, help="video channel (default 1)")

    p = add_command(sub, "set-recording", "Set recording schedule and pre/post-record times",
                    cmd_set_recording, parents, writes=True,
                    change_opts=["enable", "pre_record", "post_record", "mode", "audio"],
                    epilog=("--mode replaces the whole weekly schedule with one time window per day.\n"
                            "Example: set-recording -c cams.yaml --mode motion --days weekdays "
                            "--start 08:00 --end 18:00"))
    p.add_argument("--stream", choices=["main", "sub"], default="main", help="recording track")
    p.add_argument("--channel", type=int, help="video channel (default 1)")
    p.add_argument("--enable", type=on_off, metavar="on|off", help="enable/disable scheduled recording")
    p.add_argument("--mode", choices=list(MODES), help="recording type for the new schedule")
    p.add_argument("--days", default=None, help="all | weekdays | weekends | Mon,Wed,Fri (default all)")
    p.add_argument("--start", default="00:00", help="window start HH:MM (default 00:00)")
    p.add_argument("--end", default="24:00", help="window end HH:MM (default 24:00)")
    p.add_argument("--pre-record", type=int_range(0, 30), metavar="SEC", help="pre-record seconds")
    p.add_argument("--post-record", type=int_range(0, 600), metavar="SEC", help="post-record seconds")
    p.add_argument("--audio", type=on_off, metavar="on|off", help="record audio with video")
