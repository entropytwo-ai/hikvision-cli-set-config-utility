"""System: device info, status, device name, reboot, factory reset, config backup.

Endpoints:
  GET     /ISAPI/System/deviceInfo
  PUT     /ISAPI/System/deviceInfo          (device name)
  GET     /ISAPI/System/status
  PUT     /ISAPI/System/reboot
  PUT     /ISAPI/System/factoryReset?mode=basic|full
  GET     /ISAPI/System/configurationData   (binary configuration backup)
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict

from .camera_client import CameraClient, set_text, text
from .common import add_command

INFO_PATH = "/ISAPI/System/deviceInfo"
STATUS_PATH = "/ISAPI/System/status"
REBOOT_PATH = "/ISAPI/System/reboot"
RESET_PATH = "/ISAPI/System/factoryReset"
CONFIG_PATH = "/ISAPI/System/configurationData"


class System:
    def __init__(self, client: CameraClient):
        self.client = client

    def get_info(self) -> Dict[str, Any]:
        root = self.client.get_xml(INFO_PATH)
        fields = {
            "device_name": "deviceName", "model": "model", "serial_number": "serialNumber",
            "mac_address": "macAddress", "firmware_version": "firmwareVersion",
            "firmware_date": "firmwareReleasedDate", "encoder_version": "encoderVersion",
            "hardware_version": "hardwareVersion", "device_type": "deviceType",
            "device_id": "deviceID",
        }
        return {k: text(root, tag) for k, tag in fields.items() if text(root, tag) is not None}

    def get_status(self) -> Dict[str, Any]:
        root = self.client.get_xml(STATUS_PATH)
        uptime = text(root, "deviceUpTime")
        result: Dict[str, Any] = {"current_time": text(root, "currentDeviceTime")}
        if uptime and uptime.isdigit():
            secs = int(uptime)
            result["uptime"] = f"{secs // 86400}d {secs % 86400 // 3600}h {secs % 3600 // 60}m"
        cpu = root.find(".//CPU/cpuUtilization")
        if cpu is not None:
            result["cpu_percent"] = cpu.text
        mem = root.find(".//Memory")
        if mem is not None:
            result["memory_used_mb"] = text(mem, "memoryUsage")
            result["memory_free_mb"] = text(mem, "memoryAvailable")
        return {k: v for k, v in result.items() if v is not None}

    def set_device_name(self, name: str) -> None:
        root = self.client.get_xml(INFO_PATH)
        set_text(root, "deviceName", name)
        self.client.put_xml(INFO_PATH, root)

    def reboot(self) -> None:
        self.client.put_xml(REBOOT_PATH, None)

    def factory_reset(self, mode: str = "basic") -> None:
        self.client.put_xml(RESET_PATH, None, params={"mode": mode})

    def backup_config(self, directory: str, label: str) -> str:
        resp = self.client.request("GET", CONFIG_PATH)
        os.makedirs(directory, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", label)
        path = os.path.join(directory, f"{safe}_{datetime.now():%Y%m%d-%H%M%S}.bin")
        with open(path, "wb") as fh:
            fh.write(resp.content)
        return path


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def cmd_get_info(client, log, args, camera) -> None:
    with log.step("retrieve device info"):
        info = System(client).get_info()
        log.ok("Device info retrieved successfully")
        log.data("Device info", info, "device_info")


def cmd_get_status(client, log, args, camera) -> None:
    with log.step("retrieve device status"):
        info = System(client).get_status()
        log.ok("Device status retrieved successfully")
        log.data("Device status", info, "device_status")


def cmd_set_device_name(client, log, args, camera) -> None:
    name = args.name.replace("{ip}", camera.ip).replace("{name}", camera.name or camera.ip)
    with log.step("set device name"):
        System(client).set_device_name(name)
        log.ok(f"Device name set to: {name}")


def cmd_reboot(client, log, args, camera) -> None:
    with log.step("reboot device"):
        System(client).reboot()
        log.ok("Reboot command accepted - device will be offline for 1-3 minutes")


def cmd_restore_defaults(client, log, args, camera) -> None:
    with log.step(f"restore defaults ({args.mode})"):
        System(client).factory_reset(args.mode)
        if args.mode == "full":
            log.ok("Full factory reset accepted - device will return to its default IP and "
                   "must be re-activated")
        else:
            log.ok("Basic restore accepted - settings reset except network and user accounts")


def cmd_backup_config(client, log, args, camera) -> None:
    with log.step("download configuration backup"):
        if client.dry_run:
            log.ok("Configuration backup would be downloaded")
            return
        path = System(client).backup_config(args.output_dir, f"{camera.ip}_{camera.effective_port}")
        log.ok(f"Configuration saved to: {path}")


def _require_confirm(args) -> None:
    if not args.confirm:
        raise SystemExit("restore-defaults is destructive: re-run with --confirm to proceed")


def register(sub, parents) -> None:
    add_command(sub, "get-info", "Read model, serial number, firmware version and device name",
                cmd_get_info, parents)
    add_command(sub, "get-status", "Read uptime, CPU and memory usage", cmd_get_status, parents)

    p = add_command(sub, "set-device-name", "Set the device name", cmd_set_device_name, parents,
                    writes=True, epilog="Placeholders: {ip}, {name} (camera file 'name' field)")
    p.add_argument("--name", required=True, help="new device name, e.g. \"CAM-{ip}\"")

    add_command(sub, "reboot", "Reboot the device", cmd_reboot, parents, writes=True)

    p = add_command(sub, "restore-defaults", "Restore factory defaults (requires --confirm)",
                    cmd_restore_defaults, parents, writes=True,
                    epilog=("basic: keeps network settings and user accounts (recommended)\n"
                            "full : complete factory reset - camera returns to 192.168.1.64 and\n"
                            "       must be re-activated"))
    p.add_argument("--mode", choices=["basic", "full"], default="basic",
                   help="basic (keep network/users, default) or full")
    p.add_argument("--confirm", action="store_true", help="required safety flag")
    p.set_defaults(_post_parse=_require_confirm)

    p = add_command(sub, "backup-config", "Download a binary configuration backup per camera",
                    cmd_backup_config, parents)
    p.add_argument("--output-dir", default="backups", help="directory for backups (default backups/)")
