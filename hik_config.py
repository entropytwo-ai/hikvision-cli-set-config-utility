#!/usr/bin/env python3
"""hik-config - bulk get/set configuration on Hikvision cameras via ISAPI.

Run ``python3 hik_config.py --help`` for the command list and
``python3 hik_config.py <command> --help`` for the options of each command.
"""
from __future__ import annotations

import argparse
import os
import sys
from getpass import getpass
from typing import List

import threading

from modules import (discovery, email_alerts, motion, network, ntp, osd, ptz, recording, system,
                     time_date, users, video)
from modules.camera_client import element_to_dict, parse_xml
from modules.camera_loader import (Camera, CameraFileError, expand_ip_range, load_cameras,
                                    write_camera_file)
from modules.worker import RunOptions, Runner, set_color
from modules.worker import _Colors as Colors

__version__ = "2.1.0"

FEATURE_MODULES = [ntp, time_date, network, video, recording, osd, motion, users, system,
                   email_alerts, ptz]
DANGEROUS = {"reboot", "restore-defaults", "set-network", "delete-user"}

# Read-only handlers executed by `get-all`, in display order.
GET_ALL = [system.cmd_get_info, time_date.cmd_get_time, ntp.cmd_get_ntp, network.cmd_get_network,
           video.cmd_get_video, recording.cmd_get_recording, osd.cmd_get_osd, motion.cmd_get_motion,
           email_alerts.cmd_get_email, users.cmd_list_users]


# ─────────────────────────────────────────────────────────────────────────────
# Built-in commands
# ─────────────────────────────────────────────────────────────────────────────

def cmd_get_all(client, log, args, camera) -> None:
    channel = getattr(args, "channel", None)
    for handler in GET_ALL:
        stream = "main" if handler is recording.cmd_get_recording else None
        handler(client, log, argparse.Namespace(channel=channel, stream=stream, server_id=1,
                                                interface=1), camera)
        if client.halted:
            log.info("Remaining checks skipped (camera unreachable or login failed)")
            break


def cmd_raw(client, log, args, camera) -> None:
    body = None
    if args.data_file:
        with open(args.data_file, encoding="utf-8") as fh:
            body = fh.read()
    with log.step(f"{args.method} {args.path}"):
        resp = client.request(args.method, args.path, data=body)
        log.ok(f"{args.method} {args.path} -> HTTP {resp.status_code or 200}")
        if resp.text and resp.text.lstrip().startswith("<"):
            log.data("Response", element_to_dict(parse_xml(resp.text)), "response")
        elif resp.text:
            log.data("Response", resp.text[:2000], "response")


# ─────────────────────────────────────────────────────────────────────────────
# scan - discover cameras and build a camera list
# ─────────────────────────────────────────────────────────────────────────────

def _scan_line(dev, color: bool) -> str:
    paint = Colors.paint if color else (lambda t, k: t)
    if dev.authenticated:
        return "  " + paint(f"✓ {dev.label}  {dev.summary_line()}", "ok")
    if dev.needs_auth:
        return "  " + paint(f"! {dev.label}  {dev.summary_line()} - {dev.note}", "warn")
    if dev.is_camera:
        return "  " + paint(f"✓ {dev.label}  {dev.summary_line()}", "ok")
    if dev.tcp_open:
        return "  " + paint(f"• {dev.label}  {dev.note}", "info")
    return "  " + paint(f"· {dev.label}  {dev.note}", "info")


def run_scan(args) -> int:
    color = not args.no_color and sys.stdout.isatty()
    set_color(color)
    paint = Colors.paint if color else (lambda t, k: t)
    log_file = None if args.no_log_file else args.log_file
    print_lock = threading.Lock()

    def logline(msg: str) -> None:
        if log_file:
            with open(log_file, "a", encoding="utf-8") as fh:
                fh.write(f"{__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")

    try:
        ports = discovery.parse_ports(args.ports)
    except ValueError as exc:
        print(f"Error: --ports {args.ports}: {exc}", file=sys.stderr)
        return 2

    targets: List[tuple] = []
    if args.ip:
        try:
            hosts = discovery.expand_targets(args.ip)
        except ValueError as exc:
            print(f"Error: invalid scan target: {exc}", file=sys.stderr)
            return 2
        targets = [(ip, port) for ip in hosts for port in ports]

    if not targets and not args.sadp:
        print("Error: nothing to scan: give --ip IP|RANGE|CIDR and/or --sadp", file=sys.stderr)
        return 2

    username = args.username
    password = args.password or os.environ.get("HIK_PASSWORD")
    if username and not password and sys.stdin.isatty():
        password = getpass("Camera password (blank to probe without authenticating): ") or None

    print("═" * 60)
    print(paint(f"SCAN  ({len(targets)} target(s)"
                f"{', +SADP' if args.sadp else ''})", "head"))
    print("═" * 60)
    logline(f"=== hik-config 'scan' started: {len(targets)} target(s)"
            f"{', SADP' if args.sadp else ''} ===")

    devices = []
    seen_keys = set()

    def record(dev) -> None:
        with print_lock:
            key = (dev.ip, dev.port)
            if key in seen_keys:
                return
            seen_keys.add(key)
            interesting = dev.is_camera or dev.tcp_open
            if interesting or args.show_all:
                print(_scan_line(dev, color), flush=True)
            if log_file:
                status = ("camera" if dev.authenticated else "needs-auth" if dev.needs_auth
                          else "isapi" if dev.is_camera else "open" if dev.tcp_open else "closed")
                logline(f"[{dev.label}] {status} - {dev.summary_line()} ({dev.note})")

    # Active probe
    if targets:
        devices.extend(discovery.active_scan(
            targets, https=args.https, timeout=args.timeout, tcp_timeout=args.tcp_timeout,
            username=username, password=password, auth_mode=args.auth, verify_tls=args.verify_tls,
            workers=args.workers, on_result=record))

    # SADP multicast
    if args.sadp:
        print(paint("\n  SADP multicast discovery (local segment only)...", "info"))
        try:
            sadp_devs = discovery.sadp_discover(timeout=args.sadp_timeout, on_result=record)
            devices.extend(sadp_devs)
            if not sadp_devs:
                print(paint("  (no SADP replies - no devices on this segment, or multicast blocked)",
                            "info"))
        except discovery.SADPError as exc:
            print(paint(f"  ✗ SADP unavailable: {exc}", "fail"))
            logline(f"SADP error: {exc}")

    devices = _dedupe_devices(devices)
    cameras = [d for d in devices if d.is_camera]
    authed = [d for d in cameras if d.authenticated]
    need_auth = [d for d in cameras if d.needs_auth]
    open_non_cam = [d for d in devices if d.tcp_open and not d.is_camera]

    # Write camera list
    written = None
    if args.out:
        records = discovery.devices_to_records(
            cameras, username=username, password=(password if args.save_credentials else None),
            include_meta=not args.no_meta)
        if records:
            try:
                write_camera_file(args.out, records,
                                  header_comment=f"Discovered by hik-config scan "
                                                 f"({len(records)} camera(s))")
                written = args.out
            except CameraFileError as exc:
                print(paint(f"\n  ✗ Could not write {args.out}: {exc}", "fail"), file=sys.stderr)
        else:
            print(paint(f"\n  ! No cameras discovered - {args.out} not written.", "warn"))

    # Summary
    print()
    print("═" * 60)
    print(paint("SCAN SUMMARY", "head"))
    print("═" * 60)
    if targets:
        print(f"  Targets probed    : {len(targets)} ({len(discovery.expand_targets(args.ip))} "
              f"host(s) x {len(ports)} port(s))")
    print(paint(f"  Cameras found     : {len(cameras)}", "ok" if cameras else "info"))
    print(f"    - readable       : {len(authed)}")
    print(paint(f"    - need login     : {len(need_auth)}", "warn" if need_auth else "info"))
    print(f"  Other open hosts  : {len(open_non_cam)}")
    if need_auth and not (username and password):
        print(paint("  Tip: re-run with -u/-p to read model/firmware for protected cameras.",
                    "info"))
    if written:
        print(paint(f"  Camera list       : {written}", "ok"))
    elif args.out:
        print(f"  Camera list       : (not written)")
    if log_file:
        print(f"  Log file          : {log_file}")
    print("═" * 60)
    logline(f"=== hik-config 'scan' finished: {len(cameras)} camera(s), "
            f"{len(need_auth)} need login ===")

    if args.json_out:
        _write_scan_json(args.json_out, devices, targets, ports)
        print(f"  JSON results written to {args.json_out}")

    return 0 if cameras or not targets else 1


def _dedupe_devices(devices):
    """Collapse duplicate (ip, port) entries (e.g. active probe + SADP), keeping
    the most informative one (authenticated > camera > open)."""
    def rank(d):
        return (d.authenticated, d.is_camera, d.tcp_open)
    best = {}
    order = []
    for dev in devices:
        key = (dev.ip, dev.port)
        if key not in best:
            order.append(key)
            best[key] = dev
        elif rank(dev) > rank(best[key]):
            best[key] = dev
    return [best[k] for k in order]


def _write_scan_json(path: str, devices, targets, ports) -> None:
    import json
    from datetime import datetime
    payload = {
        "command": "scan",
        "generated": datetime.now().isoformat(timespec="seconds"),
        "targets_probed": len(targets),
        "ports": ports,
        "cameras_found": sum(1 for d in devices if d.is_camera),
        "devices": [
            {
                "ip": d.ip, "port": d.port, "https": d.https, "source": d.source,
                "tcp_open": d.tcp_open, "is_camera": d.is_camera, "needs_auth": d.needs_auth,
                "authenticated": d.authenticated, "vendor": d.vendor, "model": d.model,
                "name": d.name, "serial": d.serial, "firmware": d.firmware, "mac": d.mac,
                "device_type": d.device_type, "server": d.server, "note": d.note,
            }
            for d in devices
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────────

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    src = common.add_argument_group("camera selection")
    src.add_argument("-c", "--cameras", metavar="FILE",
                     help="camera list file (.csv, .yaml/.yml or .json)")
    src.add_argument("--ip", action="append", metavar="IP|RANGE",
                     help="camera IP, range (192.168.1.10-20) or CIDR; repeatable")
    src.add_argument("-u", "--username", help="username for cameras without one in the file")
    src.add_argument("-p", "--password",
                     help="password for cameras without one (or env HIK_PASSWORD; prompted if missing)")
    src.add_argument("--port", type=int, help="HTTP(S) port for --ip cameras (default 80/443)")

    conn = common.add_argument_group("connection")
    conn.add_argument("--https", action="store_true", help="use HTTPS")
    conn.add_argument("--verify-tls", action="store_true", help="verify HTTPS certificates")
    conn.add_argument("--auth", choices=["auto", "digest", "basic"], default="auto",
                      help="HTTP auth method (default auto: digest, falling back to basic)")
    conn.add_argument("--timeout", type=float, default=5.0, help="request timeout seconds (default 5)")
    conn.add_argument("-w", "--workers", type=int, default=8,
                      help="cameras processed in parallel (default 8)")

    out = common.add_argument_group("output")
    out.add_argument("--log-file", default="hik-config.log", help="log file (default hik-config.log)")
    out.add_argument("--no-log-file", action="store_true", help="do not write a log file")
    out.add_argument("--json-out", metavar="FILE", help="also write all results to a JSON file")
    out.add_argument("--no-color", action="store_true", help="disable coloured output")
    out.add_argument("-q", "--quiet", action="store_true",
                     help="only print cameras with failures (summary always shown)")

    safety = common.add_argument_group("safety")
    safety.add_argument("--dry-run", action="store_true",
                        help="read current settings but do not send any changes")
    safety.add_argument("-y", "--yes", action="store_true",
                        help="do not ask for confirmation on disruptive commands")

    parser = argparse.ArgumentParser(
        prog="hik_config.py",
        description="Bulk read/write Hikvision camera configuration over ISAPI.",
        epilog="Example: python3 hik_config.py set-ntp -c cameras.yaml --server pool.ntp.org",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    for module in FEATURE_MODULES:
        module.register(sub, [common])

    from modules.common import add_command
    p = add_command(sub, "get-all", "Run every read-only get-* command (full inventory)",
                    cmd_get_all, [common])
    p.add_argument("--channel", type=int, help="video channel (default 1)")
    p = add_command(sub, "raw", "Send an arbitrary ISAPI request (advanced)", cmd_raw, [common],
                    epilog="Example: raw --method GET --path /ISAPI/System/capabilities")
    p.add_argument("--method", type=str.upper, default="GET",
                   choices=["GET", "PUT", "POST", "DELETE"])
    p.add_argument("--path", required=True, help="ISAPI path, e.g. /ISAPI/System/deviceInfo")
    p.add_argument("--data-file", help="XML file to send as the request body")
    p.set_defaults(_post_parse=lambda a: setattr(a, "writes", a.method != "GET"))

    build_scan_command(sub)
    return parser, sub


def build_scan_command(sub) -> argparse.ArgumentParser:
    p = sub.add_parser(
        "scan",
        help="Discover cameras on the network and build a camera list",
        description="Scan IPs/subnets for ISAPI-compatible cameras (Hikvision and OEM "
                    "clones) and optionally save the result as a camera list file.",
        epilog="Examples:\n"
               "  python3 hik_config.py scan --ip 192.168.1.0/24 -o cameras.yaml\n"
               "  python3 hik_config.py scan --ip 192.168.1.10-50 --ports 80,443 -u admin -p PASS "
               "-o cams.csv\n"
               "  python3 hik_config.py scan --sadp            # local-segment multicast discovery",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.set_defaults(command="scan")

    tgt = p.add_argument_group("scan targets")
    tgt.add_argument("--ip", action="append", metavar="IP|RANGE|CIDR",
                     help="IP, range (192.168.1.10-50) or CIDR (192.168.1.0/24); repeatable")
    tgt.add_argument("--ports", default="80",
                     help="port(s) to probe: list/range, e.g. 80,443,8000-8010 (default 80)")
    tgt.add_argument("--sadp", action="store_true",
                     help="also run Hikvision SADP multicast discovery (local segment only)")
    tgt.add_argument("--sadp-timeout", type=float, default=4.0,
                     help="seconds to listen for SADP replies (default 4)")

    cred = p.add_argument_group("credentials (optional - enables reading model/firmware)")
    cred.add_argument("-u", "--username", help="username to authenticate to protected cameras")
    cred.add_argument("-p", "--password",
                      help="password (or env HIK_PASSWORD; prompted if username given without one)")
    cred.add_argument("--auth", choices=["auto", "digest", "basic"], default="auto",
                      help="HTTP auth method (default auto)")

    conn = p.add_argument_group("connection")
    conn.add_argument("--https", action="store_true", help="probe over HTTPS")
    conn.add_argument("--verify-tls", action="store_true", help="verify HTTPS certificates")
    conn.add_argument("--timeout", type=float, default=4.0,
                      help="per-host HTTP timeout seconds (default 4)")
    conn.add_argument("--tcp-timeout", type=float, default=None,
                      help="TCP pre-check timeout seconds (default min(timeout, 2))")
    conn.add_argument("-w", "--workers", type=int, default=32,
                      help="hosts probed in parallel (default 32)")

    out = p.add_argument_group("output")
    out.add_argument("-o", "--out", metavar="FILE",
                     help="write discovered cameras to a .csv/.yaml/.json file")
    out.add_argument("--save-credentials", action="store_true",
                     help="include the -u/-p password in the written camera file")
    out.add_argument("--no-meta", action="store_true",
                     help="omit model/serial/firmware/mac columns from the camera file")
    out.add_argument("--json-out", metavar="FILE", help="write the full scan report as JSON")
    out.add_argument("--show-all", action="store_true",
                     help="also list hosts with no open port (closed/unreachable)")
    out.add_argument("--no-color", action="store_true", help="disable coloured output")
    out.add_argument("--log-file", default="hik-config.log",
                     help="log file (default hik-config.log)")
    out.add_argument("--no-log-file", action="store_true", help="do not write a log file")
    return p


def _given(args, sub, dest: str) -> bool:
    parser = sub.choices[args.command]
    return getattr(args, dest, None) != parser.get_default(dest)


def resolve_cameras(args, parser) -> List[Camera]:
    cameras: List[Camera] = []
    if args.cameras:
        cameras.extend(load_cameras(args.cameras))
    for spec in args.ip or []:
        try:
            for ip in expand_ip_range(spec):
                cameras.append(Camera(ip=ip, port=args.port, https=args.https))
        except ValueError as exc:
            parser.error(f"--ip {spec}: {exc}")
    if not cameras:
        parser.error("no cameras given: use -c/--cameras FILE and/or --ip")

    username = args.username
    password = args.password or os.environ.get("HIK_PASSWORD")
    if any(c.username is None for c in cameras) and not username:
        username = input("Camera username: ").strip()
    if any(c.password is None for c in cameras) and not password:
        password = getpass("Camera password: ")
    for cam in cameras:
        cam.username = cam.username or username
        cam.password = cam.password or password
    return cameras


def confirm(args, cameras: List[Camera]) -> bool:
    if args.yes or args.dry_run or args.command not in DANGEROUS:
        return True
    if not sys.stdin.isatty():
        print(f"'{args.command}' is disruptive; add --yes to run non-interactively.", file=sys.stderr)
        return False
    answer = input(f"Run '{args.command}' on {len(cameras)} camera(s)? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def main(argv=None) -> int:
    parser, sub = build_parser()
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if args.command == "scan":
        return run_scan(args)

    # "set-*" commands must change something.
    if args.change_opts and not any(_given(args, sub, d) for d in args.change_opts):
        sub.choices[args.command].error(
            "nothing to change - give at least one of: "
            + ", ".join("--" + d.replace("_", "-") for d in args.change_opts))
    if getattr(args, "_post_parse", None):
        args._post_parse(args)

    try:
        cameras = resolve_cameras(args, parser)
    except CameraFileError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    for dest in getattr(args, "single_camera_opts", []):
        if _given(args, sub, dest) and len(cameras) > 1:
            print(f"Error: --{dest.replace('_', '-')} can only be used with a single camera "
                  "(use a per-camera column in the camera file instead)", file=sys.stderr)
            return 2
    if not confirm(args, cameras):
        print("Aborted.")
        return 1

    options = RunOptions(
        workers=args.workers, timeout=args.timeout, auth_mode=args.auth, use_https=args.https,
        verify_tls=args.verify_tls, dry_run=args.dry_run,
        log_file=None if args.no_log_file else args.log_file, json_out=args.json_out,
        color=not args.no_color and sys.stdout.isatty(), quiet=args.quiet, command=args.command,
    )
    summary = Runner(options).run(cameras, args.handler, args)
    return 0 if summary.failures == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
