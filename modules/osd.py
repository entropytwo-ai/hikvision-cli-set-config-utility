"""On-screen display (OSD).

Endpoints:
  /ISAPI/System/Video/inputs/channels/<ch>                 channel (camera) name text
  /ISAPI/System/Video/inputs/channels/<ch>/overlays        name / date-time overlay flags & positions
  /ISAPI/System/Video/inputs/channels/<ch>/overlays/text/<id>   custom text lines
OSD coordinates are in Hikvision's 704x576 normalised grid (origin bottom-left on
most firmware).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, List

from .camera_client import (CameraClient, ISAPIError, add_children, find, new_element, set_text,
                            text)
from .common import add_command, channel_of, int_range, on_off, position

CHANNEL_PATH = "/ISAPI/System/Video/inputs/channels/{ch}"
OVERLAYS_PATH = "/ISAPI/System/Video/inputs/channels/{ch}/overlays"
TEXT_PATH = "/ISAPI/System/Video/inputs/channels/{ch}/overlays/text/{id}"

DATE_STYLES = ["MM-DD-YYYY", "DD-MM-YYYY", "YYYY-MM-DD", "CHR-YYYY-MM-DD", "CHR-MM-DD-YYYY",
               "CHR-DD-MM-YYYY"]


class OSD:
    def __init__(self, client: CameraClient, channel: int = 1):
        self.client = client
        self.channel = channel

    def get(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        try:
            ch = self.client.get_xml(CHANNEL_PATH.format(ch=self.channel))
            result["channel_name"] = text(ch, "name")
        except ISAPIError:
            pass
        root = self.client.get_xml(OVERLAYS_PATH.format(ch=self.channel))
        name = find(root, "channelNameOverlay")
        dt = find(root, "DateTimeOverlay")
        result["show_channel_name"] = text(name, "enabled")
        result["channel_name_position"] = _pos(name)
        result["show_date_time"] = text(dt, "enabled")
        result["date_time_position"] = _pos(dt)
        result["date_style"] = text(dt, "dateStyle")
        result["time_style"] = text(dt, "timeStyle")
        result["display_week"] = text(dt, "displayWeek")
        texts = []
        for t in root.iter("TextOverlay"):
            texts.append({"id": text(t, "id"), "enabled": text(t, "enabled"),
                          "text": text(t, "displayText"), "position": _pos(t)})
        result["text_overlays"] = texts
        return result

    def set_channel_name(self, name: str) -> None:
        path = CHANNEL_PATH.format(ch=self.channel)
        root = self.client.get_xml(path)
        set_text(root, "name", name)
        self.client.put_xml(path, root)

    def set_overlays(self, show_name=None, name_pos=None, show_datetime=None, datetime_pos=None,
                     date_style=None, time_style=None, display_week=None) -> List[str]:
        path = OVERLAYS_PATH.format(ch=self.channel)
        root = self.client.get_xml(path)
        changes: List[str] = []
        if show_name is not None:
            set_text(root, "channelNameOverlay/enabled", show_name)
            changes.append(f"channel name overlay {'on' if show_name else 'off'}")
        if name_pos:
            set_text(root, "channelNameOverlay/positionX", name_pos[0])
            set_text(root, "channelNameOverlay/positionY", name_pos[1])
            changes.append(f"channel name position {name_pos[0]},{name_pos[1]}")
        if show_datetime is not None:
            set_text(root, "DateTimeOverlay/enabled", show_datetime)
            changes.append(f"date/time overlay {'on' if show_datetime else 'off'}")
        if datetime_pos:
            set_text(root, "DateTimeOverlay/positionX", datetime_pos[0])
            set_text(root, "DateTimeOverlay/positionY", datetime_pos[1])
            changes.append(f"date/time position {datetime_pos[0]},{datetime_pos[1]}")
        if date_style:
            set_text(root, "DateTimeOverlay/dateStyle", date_style)
            changes.append(f"date style {date_style}")
        if time_style:
            set_text(root, "DateTimeOverlay/timeStyle", time_style)
            changes.append(f"time style {time_style}")
        if display_week is not None:
            set_text(root, "DateTimeOverlay/displayWeek", display_week)
            changes.append(f"weekday display {'on' if display_week else 'off'}")
        if changes:
            self.client.put_xml(path, root)
        return changes

    def set_text_overlay(self, text_id: int, value=None, enabled=None, pos=None) -> List[str]:
        path = TEXT_PATH.format(ch=self.channel, id=text_id)
        try:
            root = self.client.get_xml(path)
        except ISAPIError as exc:
            if exc.status_code not in (404, 400, 403):
                raise
            root = new_element("TextOverlay")
            add_children(root, {"id": text_id, "enabled": True, "positionX": 16,
                                "positionY": 32 * text_id, "displayText": ""})
        changes: List[str] = []
        if value is not None:
            set_text(root, "displayText", value)
            changes.append(f"text #{text_id} '{value}'")
            if enabled is None:
                enabled = bool(value)
        if enabled is not None:
            set_text(root, "enabled", enabled)
            changes.append(f"text #{text_id} {'shown' if enabled else 'hidden'}")
        if pos:
            set_text(root, "positionX", pos[0])
            set_text(root, "positionY", pos[1])
            changes.append(f"text #{text_id} position {pos[0]},{pos[1]}")
        self.client.put_xml(path, root)
        return changes


def _pos(el: ET.Element):
    if el is None:
        return None
    x, y = text(el, "positionX"), text(el, "positionY")
    return f"{x},{y}" if x is not None and y is not None else None


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def cmd_get_osd(client, log, args, camera) -> None:
    with log.step("retrieve OSD config"):
        info = OSD(client, channel_of(args, camera)).get()
        log.ok("OSD config retrieved successfully")
        log.data("OSD", info, "osd")


def cmd_set_osd(client, log, args, camera) -> None:
    osd = OSD(client, channel_of(args, camera))
    name = args.channel_name
    if name:
        name = name.replace("{ip}", camera.ip).replace("{name}", camera.name or camera.ip)
    if name:
        with log.step("set OSD channel name"):
            osd.set_channel_name(name)
            log.ok(f"OSD channel name set to: {name}")
    overlay_opts = [args.show_name, args.name_pos, args.datetime, args.datetime_pos,
                    args.date_style, args.time_style, args.display_week]
    if any(v is not None for v in overlay_opts):
        with log.step("set OSD overlays"):
            changes = osd.set_overlays(args.show_name, args.name_pos, args.datetime,
                                       args.datetime_pos, args.date_style, args.time_style,
                                       args.display_week)
            log.ok("OSD set to: " + ", ".join(changes))
    if args.text is not None or args.text_enabled is not None or args.text_pos:
        with log.step(f"set OSD text overlay #{args.text_id}"):
            changes = osd.set_text_overlay(args.text_id, args.text, args.text_enabled, args.text_pos)
            log.ok("OSD set to: " + ", ".join(changes))


def register(sub, parents) -> None:
    p = add_command(sub, "get-osd", "Read OSD channel name, date/time overlay and text overlays",
                    cmd_get_osd, parents)
    p.add_argument("--channel", type=int, help="video channel (default 1)")

    p = add_command(sub, "set-osd", "Set OSD camera name, date/time overlay, text and positions",
                    cmd_set_osd, parents, writes=True,
                    change_opts=["channel_name", "show_name", "name_pos", "datetime", "datetime_pos",
                                 "date_style", "time_style", "display_week", "text", "text_enabled",
                                 "text_pos"],
                    epilog=("--channel-name supports the placeholders {ip} and {name} (camera file\n"
                            "'name' field), e.g. --channel-name \"Lobby {ip}\"."))
    p.add_argument("--channel", type=int, help="video channel (default 1)")
    p.add_argument("--channel-name", metavar="TEXT", help="camera/channel name shown on screen")
    p.add_argument("--show-name", type=on_off, metavar="on|off", help="show the channel name")
    p.add_argument("--name-pos", type=position, metavar="X,Y", help="channel name position")
    p.add_argument("--datetime", type=on_off, metavar="on|off", help="show the date/time overlay")
    p.add_argument("--datetime-pos", type=position, metavar="X,Y", help="date/time position")
    p.add_argument("--date-style", choices=DATE_STYLES, help="date format")
    p.add_argument("--time-style", choices=["12hour", "24hour"], help="12 or 24 hour clock")
    p.add_argument("--display-week", type=on_off, metavar="on|off", help="show day of week")
    p.add_argument("--text", metavar="TEXT", help="custom text overlay content ('' to clear)")
    p.add_argument("--text-id", type=int_range(1, 8), default=1, help="text overlay slot (default 1)")
    p.add_argument("--text-enabled", type=on_off, metavar="on|off", help="show/hide the text overlay")
    p.add_argument("--text-pos", type=position, metavar="X,Y", help="text overlay position")
