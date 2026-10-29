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

from modules import (email_alerts, motion, network, ntp, osd, ptz, recording, system, time_date,
                     users, video)
from modules.camera_client import element_to_dict, parse_xml
from modules.camera_loader import Camera, CameraFileError, expand_ip_range, load_cameras
from modules.worker import RunOptions, Runner

__version__ = "2.0.0"

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
    return parser, sub


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
