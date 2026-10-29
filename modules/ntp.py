"""NTP configuration (ISAPI /ISAPI/System/time/ntpServers).

set-ntp keeps the proven approach of the original set-hik-ntp.py script:

1. read the camera's current timezone string and reuse it verbatim,
2. switch timeMode to NTP with the minimal GUI-style <Time> payload,
3. PUT the NTP server details to /ISAPI/System/time/ntpServers/1.
"""
from __future__ import annotations

from typing import Any, Dict

from .camera_client import CameraClient, is_ipv4, text, xml_escape, HIK_NS_V20
from .common import add_command, int_range, on_off
from .time_date import TimeDate

NTP_PATH = "/ISAPI/System/time/ntpServers/{id}"


class NTP:
    def __init__(self, client: CameraClient):
        self.client = client

    def get(self, server_id: int = 1) -> Dict[str, Any]:
        root = self.client.get_xml(NTP_PATH.format(id=server_id))
        fmt = text(root, "addressingFormatType")
        address = text(root, "hostName") if fmt == "hostname" else text(root, "ipAddress")
        if not address:
            address = text(root, "ipAddress") or text(root, "hostName") or text(root, "ipv6Address")
        return {
            "server_id": text(root, "id", str(server_id)),
            "address_type": fmt,
            "server": address,
            "port": text(root, "portNo"),
            "sync_interval_min": text(root, "synchronizeInterval"),
        }

    def set_server(self, server: str, port: int = 123, interval: int = 60, server_id: int = 1) -> None:
        if is_ipv4(server):
            addr = (f"  <addressingFormatType>ipaddress</addressingFormatType>\n"
                    f"  <ipAddress>{xml_escape(server)}</ipAddress>")
        else:
            addr = (f"  <addressingFormatType>hostname</addressingFormatType>\n"
                    f"  <hostName>{xml_escape(server)}</hostName>")
        xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
               f'<NTPServer version="2.0" xmlns="{HIK_NS_V20}">\n'
               f"  <id>{server_id}</id>\n{addr}\n"
               f"  <portNo>{port}</portNo>\n"
               f"  <synchronizeInterval>{interval}</synchronizeInterval>\n"
               f"</NTPServer>")
        self.client.put_xml(NTP_PATH.format(id=server_id), xml)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def cmd_get_ntp(client, log, args, camera) -> None:
    with log.step("retrieve NTP config"):
        info = NTP(client).get(getattr(args, "server_id", 1) or 1)
        try:
            info["time_mode"] = TimeDate(client).get_raw()["timeMode"]
            info["ntp_sync"] = "enabled" if (info["time_mode"] or "").lower() == "ntp" else "disabled"
        except Exception:
            pass
        log.ok("NTP config retrieved successfully")
        log.data("NTP", info, "ntp")


def cmd_set_ntp(client, log, args, camera) -> None:
    td = TimeDate(client)
    with log.step("read current timezone") as s:
        tz = td.get_raw()["timeZone"]
        if not tz:
            raise ValueError("camera did not return a timeZone value")
    if not s.ok:
        return

    if args.sync is not None:
        with log.step("set time mode") as s:
            mode = "NTP" if args.sync else "manual"
            td.put_minimal(mode, tz)
            log.ok(f"Time mode set to: {mode} (NTP sync {'enabled' if args.sync else 'disabled'})")
        if not s.ok:
            return

    if args.server:
        with log.step("set NTP server"):
            ntp = NTP(client)
            port, interval = args.ntp_port, args.interval
            if port is None or interval is None:
                try:
                    current = ntp.get(args.server_id)
                except Exception:
                    current = {}
                port = port or int(current.get("port") or 123)
                interval = interval or int(current.get("sync_interval_min") or 60)
            ntp.set_server(args.server, port, interval, args.server_id)
            log.ok(f"NTP server set to: {args.server}:{port} (sync every {interval} min)")
    elif args.ntp_port or args.interval:
        log.warn("--ntp-port/--interval are only applied together with --server")


def register(sub, parents) -> None:
    p = add_command(sub, "get-ntp", "Read the NTP server configuration", cmd_get_ntp, parents)
    p.add_argument("--server-id", type=int, default=1, help="NTP server slot (default 1)")

    p = add_command(sub, "set-ntp", "Set NTP server/port/interval and enable or disable NTP sync",
                    cmd_set_ntp, parents, writes=True, change_opts=["server", "sync"],
                    epilog="Example: hik_config.py set-ntp -c cameras.csv --server pool.ntp.org --sync on")
    p.add_argument("--server", help="NTP server IP address or hostname")
    p.add_argument("--ntp-port", type=int_range(1, 65535), help="NTP port (default 123)")
    p.add_argument("--interval", type=int_range(1, 10080),
                   help="sync interval in minutes (default 60)")
    p.add_argument("--sync", type=on_off, metavar="on|off",
                   help="on = time mode NTP, off = time mode manual (default: on when --server given)")
    p.add_argument("--server-id", type=int, default=1, help="NTP server slot (default 1)")
    p.set_defaults(_post_parse=_default_sync)


def _default_sync(args) -> None:
    # Mirror the original script: setting a server also switches the camera to NTP mode.
    if args.server and args.sync is None:
        args.sync = True
