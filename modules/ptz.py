"""PTZ presets (ISAPI /ISAPI/PTZCtrl/channels/<ch>/presets). Only for PTZ-capable cameras."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .camera_client import CameraClient, add_children, new_element, text
from .common import add_command, channel_of, int_range

PRESETS_PATH = "/ISAPI/PTZCtrl/channels/{ch}/presets"
PRESET_PATH = "/ISAPI/PTZCtrl/channels/{ch}/presets/{id}"
GOTO_PATH = "/ISAPI/PTZCtrl/channels/{ch}/presets/{id}/goto"


class PTZ:
    def __init__(self, client: CameraClient, channel: int = 1):
        self.client = client
        self.channel = channel

    def list_presets(self) -> List[Dict[str, Any]]:
        root = self.client.get_xml(PRESETS_PATH.format(ch=self.channel))
        presets = []
        for p in root.iter("PTZPreset"):
            enabled = text(p, "enabled")
            if enabled is not None and enabled.lower() == "false":
                continue  # unused slot
            presets.append({"id": text(p, "id"), "name": text(p, "presetName")})
        return presets

    def resolve(self, preset: str) -> str:
        """Accept a numeric id or a (case-insensitive) preset name."""
        if preset.isdigit():
            return preset
        for p in self.list_presets():
            if (p["name"] or "").lower() == preset.lower():
                return p["id"]
        raise ValueError(f"preset '{preset}' not found")

    def goto(self, preset: str) -> str:
        pid = self.resolve(preset)
        self.client.put_xml(GOTO_PATH.format(ch=self.channel, id=pid), None)
        return pid

    def save(self, preset_id: int, name: Optional[str] = None) -> None:
        root = new_element("PTZPreset")
        add_children(root, {"id": preset_id, "presetName": name or f"Preset {preset_id}"})
        self.client.put_xml(PRESET_PATH.format(ch=self.channel, id=preset_id), root)

    def delete(self, preset: str) -> str:
        pid = self.resolve(preset)
        self.client.delete(PRESET_PATH.format(ch=self.channel, id=pid))
        return pid


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def cmd_get_presets(client, log, args, camera) -> None:
    with log.step("retrieve PTZ presets"):
        presets = PTZ(client, channel_of(args, camera)).list_presets()
        log.ok(f"Retrieved {len(presets)} PTZ preset(s)")
        log.data("PTZ presets", [f"#{p['id']}  {p['name']}" for p in presets], "ptz_presets")


def cmd_call_preset(client, log, args, camera) -> None:
    with log.step(f"call PTZ preset '{args.preset}'"):
        pid = PTZ(client, channel_of(args, camera)).goto(args.preset)
        log.ok(f"PTZ moved to preset #{pid} ({args.preset})")


def cmd_set_preset(client, log, args, camera) -> None:
    with log.step(f"save PTZ preset #{args.id}"):
        PTZ(client, channel_of(args, camera)).save(args.id, args.name)
        log.ok(f"Current position saved as preset #{args.id}"
               + (f" ('{args.name}')" if args.name else ""))


def cmd_delete_preset(client, log, args, camera) -> None:
    with log.step(f"delete PTZ preset '{args.preset}'"):
        pid = PTZ(client, channel_of(args, camera)).delete(args.preset)
        log.ok(f"PTZ preset #{pid} deleted")


def register(sub, parents) -> None:
    p = add_command(sub, "get-ptz-presets", "List PTZ presets", cmd_get_presets, parents)
    p.add_argument("--channel", type=int, help="PTZ channel (default 1)")

    p = add_command(sub, "call-preset", "Move the camera to a PTZ preset (by id or name)",
                    cmd_call_preset, parents, writes=True)
    p.add_argument("--preset", required=True, help="preset id or name")
    p.add_argument("--channel", type=int, help="PTZ channel (default 1)")

    p = add_command(sub, "set-preset", "Save the current PTZ position as a preset",
                    cmd_set_preset, parents, writes=True)
    p.add_argument("--id", type=int_range(1, 300), required=True, help="preset number")
    p.add_argument("--name", help="preset name")
    p.add_argument("--channel", type=int, help="PTZ channel (default 1)")

    p = add_command(sub, "delete-preset", "Delete a PTZ preset (by id or name)",
                    cmd_delete_preset, parents, writes=True)
    p.add_argument("--preset", required=True, help="preset id or name")
    p.add_argument("--channel", type=int, help="PTZ channel (default 1)")
