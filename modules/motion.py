"""Motion detection (ISAPI /ISAPI/System/Video/inputs/channels/<ch>/motionDetection).

Grid-mode cameras store the detection area as a hex ``gridMap``: one row per
grid line (usually 18 rows x 22 columns), each row padded to whole bytes, with
1 bits marking active cells. ``--full-frame`` activates every cell,
``--clear-area`` deactivates every cell.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .camera_client import CameraClient, find_any, set_text, text
from .common import add_command, channel_of, int_range, on_off

MOTION_PATH = "/ISAPI/System/Video/inputs/channels/{ch}/motionDetection"
TRIGGER_PATH = "/ISAPI/Event/triggers/VMD-{ch}"


def grid_map(rows: int, cols: int, fill: bool) -> str:
    nbytes = (cols + 7) // 8
    row_value = (((1 << cols) - 1) << (nbytes * 8 - cols)) if fill else 0
    return f"{row_value:0{nbytes * 2}x}" * rows


def count_active_cells(gmap: str, rows: int, cols: int) -> int:
    nbytes = (cols + 7) // 8
    width = nbytes * 2
    total = 0
    for r in range(rows):
        chunk = gmap[r * width:(r + 1) * width]
        if chunk:
            total += bin(int(chunk, 16) >> (nbytes * 8 - cols)).count("1")
    return total


class Motion:
    def __init__(self, client: CameraClient, channel: int = 1):
        self.client = client
        self.channel = channel
        self.path = MOTION_PATH.format(ch=channel)

    def get(self) -> Dict[str, Any]:
        root = self.client.get_xml(self.path)
        sens = find_any(root, "sensitivityLevel")
        result: Dict[str, Any] = {
            "enabled": text(root, "enabled"),
            "highlight": text(root, "enableHighlight"),
            "region_type": text(root, "regionType"),
            "sensitivity": sens.text.strip() if sens is not None and sens.text else None,
            "sampling_interval": text(root, "samplingInterval"),
            "start_trigger_time_ms": text(root, "startTriggerTime"),
            "end_trigger_time_ms": text(root, "endTriggerTime"),
        }
        rows = text(root, "Grid/rowGranularity")
        cols = text(root, "Grid/columnGranularity")
        gmap = find_any(root, "gridMap")
        if rows and cols and gmap is not None and gmap.text:
            r, c = int(rows), int(cols)
            active = count_active_cells(gmap.text.strip(), r, c)
            result["grid"] = f"{r} rows x {c} cols"
            result["active_cells"] = f"{active}/{r * c} ({100 * active // (r * c)}%)"
        regions = list(root.iter("MotionDetectionRegion"))
        if regions:
            result["regions"] = [
                {"id": text(reg, "id"), "enabled": text(reg, "enabled"),
                 "sensitivity": text(reg, "sensitivityLevel"),
                 "points": len(list(reg.iter("RegionCoordinates")))}
                for reg in regions]
        return {k: v for k, v in result.items() if v is not None}

    def set(self, enabled: Optional[bool] = None, sensitivity: Optional[int] = None,
            highlight: Optional[bool] = None, area: Optional[str] = None) -> List[str]:
        root = self.client.get_xml(self.path)
        changes: List[str] = []
        if enabled is not None:
            set_text(root, "enabled", enabled)
            changes.append("enabled" if enabled else "disabled")
        if sensitivity is not None:
            levels = list(root.iter("sensitivityLevel"))
            if not levels:
                set_text(root, "MotionDetectionLayout/sensitivityLevel", sensitivity)
            for el in levels:  # grid layout and/or every region
                el.text = str(sensitivity)
            changes.append(f"sensitivity {sensitivity}")
        if highlight is not None:
            set_text(root, "enableHighlight", highlight)
            changes.append(f"highlight {'on' if highlight else 'off'}")
        if area:
            rows = int(text(root, "Grid/rowGranularity") or 18)
            cols = int(text(root, "Grid/columnGranularity") or 22)
            gmap = find_any(root, "gridMap")
            value = grid_map(rows, cols, area == "full")
            if gmap is None:
                set_text(root, "MotionDetectionLayout/layout/gridMap", value)
            else:
                gmap.text = value
            changes.append("detection area: full frame" if area == "full" else "detection area cleared")
        self.client.put_xml(self.path, root)
        return changes

    def get_notifications(self) -> List[str]:
        root = self.client.get_xml(TRIGGER_PATH.format(ch=self.channel))
        return [m.text.strip() for m in root.iter("notificationMethod") if m.text]


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def cmd_get_motion(client, log, args, camera) -> None:
    motion = Motion(client, channel_of(args, camera))
    with log.step("retrieve motion detection config"):
        info = motion.get()
        try:
            info["linked_actions"] = ", ".join(motion.get_notifications()) or "none"
        except Exception:
            pass
        log.ok("Motion detection config retrieved successfully")
        log.data("Motion detection", info, "motion")


def cmd_set_motion(client, log, args, camera) -> None:
    area = "full" if args.full_frame else ("clear" if args.clear_area else None)
    with log.step("set motion detection config"):
        changes = Motion(client, channel_of(args, camera)).set(args.enable, args.sensitivity,
                                                               args.highlight, area)
        log.ok("Motion detection set to: " + ", ".join(changes))


def register(sub, parents) -> None:
    p = add_command(sub, "get-motion", "Read motion detection status, sensitivity and regions",
                    cmd_get_motion, parents)
    p.add_argument("--channel", type=int, help="video channel (default 1)")

    p = add_command(sub, "set-motion", "Enable/disable motion detection and set sensitivity/area",
                    cmd_set_motion, parents, writes=True,
                    change_opts=["enable", "sensitivity", "highlight", "full_frame", "clear_area"])
    p.add_argument("--channel", type=int, help="video channel (default 1)")
    p.add_argument("--enable", type=on_off, metavar="on|off", help="turn motion detection on/off")
    p.add_argument("--sensitivity", type=int_range(0, 100), help="sensitivity 0-100")
    p.add_argument("--highlight", type=on_off, metavar="on|off",
                   help="highlight moving objects in live view")
    area = p.add_mutually_exclusive_group()
    area.add_argument("--full-frame", action="store_true", help="detect motion in the whole image")
    area.add_argument("--clear-area", action="store_true", help="remove all detection areas")
