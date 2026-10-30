"""Discover ISAPI cameras on the network and build a camera list.

Two independent discovery methods are provided:

* **Active probe** (:func:`probe_target` / :func:`active_scan`) - connects to
  each IP/port and asks for ``/ISAPI/System/deviceInfo``. Works across subnets
  (routed networks), with or without credentials, and identifies any
  ISAPI-compatible device (Hikvision and the many OEM/clone brands that speak the
  same protocol). This is the primary method used by the ``scan`` command.

* **SADP multicast** (:func:`sadp_discover`) - sends a Hikvision SADP "inquiry"
  to the multicast group ``239.255.255.250:37020`` and collects the replies.
  This is the same mechanism Hikvision's own SADP tool uses. It finds devices
  even when their IP is unknown or mis-configured, but only on the **local layer-2
  segment** (multicast is not routed), so it cannot see cameras behind a router.

Both return :class:`DiscoveredDevice` records. :func:`devices_to_records` turns
them into plain dicts that :mod:`modules.camera_loader` can write back out as a
CSV/YAML/JSON camera list - so a scan result feeds straight into every other
command.

This tool probes only the exact addresses the user asks it to scan, which are
expected to be the user's own cameras. It performs an ordinary ISAPI request per
host (optionally a TCP pre-check), not a stealth port sweep.
"""
from __future__ import annotations

import ipaddress
import socket
import struct
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from .camera_client import parse_xml, text

DEVICE_INFO_PATH = "/ISAPI/System/deviceInfo"

# Server: header substrings commonly returned by ISAPI cameras / NVRs.
ISAPI_SERVER_HINTS = ("app-webs", "dnvrs-webs", "webserver", "hikvision", "web server")

# Known model-prefix -> vendor map for the widespread ISAPI ecosystem.
VENDOR_PREFIXES = {
    "DS-": "Hikvision", "IDS-": "Hikvision", "IPC-": "Hikvision OEM",
    "HWI-": "Hikvision (Hiwatch)", "HWN-": "Hikvision (Hiwatch)",
    "ERG-": "LTS", "CMIP": "LTS", "N-": "Nelly's / OEM",
}


@dataclass
class DiscoveredDevice:
    """A host found during a scan. Only ``ip``/``port`` are always populated."""

    ip: str
    port: int = 80
    https: bool = False
    tcp_open: bool = False
    is_camera: bool = False          # responds on an ISAPI endpoint
    needs_auth: bool = False         # ISAPI replied 401 (credentials required)
    authenticated: bool = False      # we successfully read deviceInfo
    vendor: Optional[str] = None
    model: Optional[str] = None
    name: Optional[str] = None
    serial: Optional[str] = None
    firmware: Optional[str] = None
    mac: Optional[str] = None
    device_type: Optional[str] = None
    server: Optional[str] = None
    source: str = "probe"            # "probe" or "sadp"
    note: Optional[str] = None

    @property
    def default_port(self) -> int:
        return 443 if self.https else 80

    @property
    def label(self) -> str:
        return self.ip if self.port == self.default_port else f"{self.ip}:{self.port}"

    def summary_line(self) -> str:
        """One-line human description, e.g. 'DS-2CD2143G2-I (fw V5.7.3) "Front Door"'."""
        bits: List[str] = []
        if self.vendor and (not self.model or not self.model.upper().startswith(
                tuple(p.rstrip("-") for p in VENDOR_PREFIXES))):
            bits.append(self.vendor)
        if self.model:
            bits.append(self.model)
        if self.firmware:
            bits.append(f"fw {self.firmware}")
        if self.name:
            bits.append(f'"{self.name}"')
        return " ".join(bits) if bits else (self.vendor or "ISAPI device")


# ─────────────────────────────────────────────────────────────────────────────
# Target expansion
# ─────────────────────────────────────────────────────────────────────────────

def parse_ports(spec: str) -> List[int]:
    """Parse a port spec like '80,443,8000-8010' into a sorted unique list."""
    ports: List[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            lo, hi = int(lo), int(hi)
            if lo > hi:
                raise ValueError(f"invalid port range '{part}'")
            ports.extend(range(lo, hi + 1))
        else:
            ports.append(int(part))
    for p in ports:
        if not 1 <= p <= 65535:
            raise ValueError(f"port out of range: {p}")
    return sorted(set(ports))


def expand_targets(specs: List[str]) -> List[str]:
    """Expand IPs, 'a.b.c.d-e[.f.g]' ranges and CIDR blocks into a host list.

    A bare ``/24`` (or any prefix) CIDR yields the usable host addresses only
    (network and broadcast are skipped by ``ipaddress``).
    """
    hosts: List[str] = []
    seen = set()

    def add(ip: str) -> None:
        if ip not in seen:
            seen.add(ip)
            hosts.append(ip)

    for spec in specs:
        spec = spec.strip()
        if "/" in spec:
            net = ipaddress.ip_network(spec, strict=False)
            if net.num_addresses <= 2:        # /31, /32 - use the address itself
                for h in net:
                    add(str(h))
            else:
                for h in net.hosts():
                    add(str(h))
        elif "-" in spec:
            start, end = spec.split("-", 1)
            start_ip = ipaddress.ip_address(start.strip())
            end = end.strip()
            if "." not in end:                 # shorthand '10.0.0.1-20'
                end = start.rsplit(".", 1)[0] + "." + end
            end_ip = ipaddress.ip_address(end)
            if int(end_ip) < int(start_ip):
                raise ValueError(f"range end before start: {spec}")
            for i in range(int(start_ip), int(end_ip) + 1):
                add(str(ipaddress.ip_address(i)))
        else:
            add(str(ipaddress.ip_address(spec)))
    return hosts


# ─────────────────────────────────────────────────────────────────────────────
# Active HTTP(S)/ISAPI probe
# ─────────────────────────────────────────────────────────────────────────────

def _tcp_open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _guess_vendor(model: Optional[str], server: Optional[str], realm: Optional[str]) -> Optional[str]:
    if model:
        up = model.upper()
        for prefix, vendor in VENDOR_PREFIXES.items():
            if up.startswith(prefix):
                return vendor
    for hint, vendor in (("hikvision", "Hikvision"),):
        if server and hint in server.lower():
            return vendor
        if realm and hint in realm.lower():
            return vendor
    return None


def _parse_device_info(xml_text: str, dev: DiscoveredDevice) -> None:
    try:
        root = parse_xml(xml_text)
    except Exception:
        return
    if "deviceinfo" not in root.tag.lower():
        return
    dev.is_camera = True
    dev.name = text(root, "deviceName") or dev.name
    dev.model = text(root, "model") or dev.model
    dev.serial = text(root, "serialNumber") or dev.serial
    dev.firmware = text(root, "firmwareVersion") or dev.firmware
    dev.mac = text(root, "macAddress") or dev.mac
    dev.device_type = text(root, "deviceType") or dev.device_type
    manufacturer = text(root, "manufacturer")
    dev.vendor = manufacturer or _guess_vendor(dev.model, dev.server, None) or dev.vendor


def probe_target(ip: str, port: int, *, https: bool = False, timeout: float = 4.0,
                 tcp_timeout: Optional[float] = None, username: Optional[str] = None,
                 password: Optional[str] = None, auth_mode: str = "auto",
                 verify_tls: bool = False) -> DiscoveredDevice:
    """Probe a single ``ip:port`` and return what was found.

    The sequence is: optional TCP pre-check -> unauthenticated GET of
    ``/ISAPI/System/deviceInfo``. A ``200`` identifies an open camera; a ``401``
    identifies a credential-protected ISAPI device (and, if credentials were
    supplied, we retry to read its details). Anything else that still answers
    HTTP is recorded as a non-ISAPI host.
    """
    dev = DiscoveredDevice(ip=ip, port=port, https=https)
    tcp_timeout = tcp_timeout if tcp_timeout is not None else min(timeout, 2.0)

    if not _tcp_open(ip, port, tcp_timeout):
        dev.note = "no TCP connection"
        return dev
    dev.tcp_open = True

    scheme = "https" if https else "http"
    host = ip if port == dev.default_port else f"{ip}:{port}"
    url = f"{scheme}://{host}{DEVICE_INFO_PATH}"
    session = requests.Session()
    session.verify = verify_tls
    session.headers.update({"User-Agent": "hik-config/2.1 (scan)"})
    if not verify_tls:
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass

    try:
        resp = session.get(url, timeout=timeout)
    except requests.RequestException as exc:
        dev.note = f"TCP open but HTTP failed ({exc.__class__.__name__})"
        session.close()
        return dev

    dev.server = resp.headers.get("Server")

    if resp.status_code == 200:
        _parse_device_info(resp.text, dev)
        if dev.is_camera:
            dev.authenticated = True
            dev.note = "open (no authentication required)"
        else:
            dev.note = f"HTTP 200 but not an ISAPI deviceInfo (Server: {dev.server or '?'})"
        session.close()
        return dev

    if resp.status_code == 401:
        dev.is_camera = True            # ISAPI endpoint exists, just protected
        dev.needs_auth = True
        realm = resp.headers.get("WWW-Authenticate", "")
        dev.vendor = _guess_vendor(None, dev.server, realm)
        if username and password:
            auth = (HTTPBasicAuth(username, password) if auth_mode == "basic"
                    else HTTPDigestAuth(username, password))
            try:
                r2 = session.get(url, auth=auth, timeout=timeout)
                if (r2.status_code == 401 and auth_mode == "auto"
                        and "basic" in realm.lower() and "digest" not in realm.lower()):
                    r2 = session.get(url, auth=HTTPBasicAuth(username, password), timeout=timeout)
                if r2.status_code == 200:
                    _parse_device_info(r2.text, dev)
                    dev.authenticated = True
                    dev.needs_auth = False
                    dev.note = "authenticated"
                elif r2.status_code == 401:
                    dev.note = "authentication required (supplied credentials rejected)"
                else:
                    dev.note = f"authentication required (HTTP {r2.status_code} with credentials)"
            except requests.RequestException as exc:
                dev.note = f"authentication required (probe error: {exc.__class__.__name__})"
        else:
            dev.note = "ISAPI device, authentication required (supply -u/-p to read details)"
        session.close()
        return dev

    # Some other HTTP status: a web server, but not an open ISAPI endpoint.
    dev.note = f"HTTP {resp.status_code} on {DEVICE_INFO_PATH} (Server: {dev.server or '?'})"
    session.close()
    return dev


def active_scan(targets: List[Tuple[str, int]], *, https: bool = False, timeout: float = 4.0,
                tcp_timeout: Optional[float] = None, username: Optional[str] = None,
                password: Optional[str] = None, auth_mode: str = "auto",
                verify_tls: bool = False, workers: int = 32,
                on_result: Optional[Callable[[DiscoveredDevice], None]] = None,
                ) -> List[DiscoveredDevice]:
    """Probe many ``(ip, port)`` targets in parallel.

    ``on_result`` (if given) is called from the worker threads as each target
    finishes, so callers can stream progress. The returned list preserves the
    input order.
    """
    results: Dict[int, DiscoveredDevice] = {}
    order = {i: t for i, t in enumerate(targets)}
    workers = max(1, min(workers, len(targets) or 1))

    def _run(index: int) -> int:
        ip, port = order[index]
        dev = probe_target(ip, port, https=https, timeout=timeout, tcp_timeout=tcp_timeout,
                           username=username, password=password, auth_mode=auth_mode,
                           verify_tls=verify_tls)
        results[index] = dev
        if on_result:
            on_result(dev)
        return index

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scan") as pool:
        futures = [pool.submit(_run, i) for i in range(len(targets))]
        for fut in as_completed(futures):
            fut.result()
    return [results[i] for i in range(len(targets))]


# ─────────────────────────────────────────────────────────────────────────────
# SADP multicast discovery (layer-2 only)
# ─────────────────────────────────────────────────────────────────────────────

SADP_GROUP = "239.255.255.250"
SADP_PORT = 37020


class SADPError(Exception):
    pass


def _sadp_parse(xml_bytes: bytes) -> Optional[DiscoveredDevice]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    def g(*tags: str) -> Optional[str]:
        for tag in tags:
            el = root.find(f".//{tag}")
            if el is not None and el.text:
                return el.text.strip()
        return None

    ip = g("IPv4Address", "ipv4Address", "IPAddress")
    if not ip:
        return None
    port_s = g("HttpPort", "httpPort", "CommandPort", "commandPort")
    try:
        port = int(port_s) if port_s else 80
    except ValueError:
        port = 80
    dev = DiscoveredDevice(ip=ip, port=port, source="sadp", tcp_open=True, is_camera=True)
    dev.model = g("DeviceDescription", "deviceDescription", "deviceType")
    dev.device_type = g("DeviceType", "deviceType")
    dev.serial = g("DeviceSN", "SerialNO", "serialNumber")
    dev.firmware = g("SoftwareVersion", "softwareVersion")
    dev.mac = g("MAC", "mac", "macAddress")
    dev.name = g("DeviceName", "deviceName")
    dev.vendor = _guess_vendor(dev.model, None, None) or "Hikvision (SADP)"
    dev.note = "found via SADP multicast"
    return dev


def sadp_discover(timeout: float = 4.0,
                  on_result: Optional[Callable[[DiscoveredDevice], None]] = None,
                  ) -> List[DiscoveredDevice]:
    """Broadcast a SADP inquiry and collect replies for ``timeout`` seconds.

    Raises :class:`SADPError` if the socket cannot be set up (e.g. the port is
    already held by Hikvision's own SADP service, or the platform forbids it).
    Only sees devices on the same local network segment.
    """
    probe = (
        '<?xml version="1.0" encoding="utf-8"?>\r\n'
        f"<Probe><Uuid>{uuid.uuid4()}</Uuid><Types>inquiry</Types></Probe>"
    ).encode()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        try:
            sock.bind(("", SADP_PORT))
        except OSError as exc:
            # Fall back to an ephemeral port; many devices still unicast a reply.
            try:
                sock.bind(("", 0))
            except OSError:
                raise SADPError(f"cannot bind a UDP socket for SADP: {exc}")
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        try:
            mreq = struct.pack("4sl", socket.inet_aton(SADP_GROUP), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            pass  # not fatal - we may still receive unicast replies
        try:
            sock.sendto(probe, (SADP_GROUP, SADP_PORT))
        except OSError as exc:
            raise SADPError(f"cannot send SADP probe: {exc}")

        found: Dict[str, DiscoveredDevice] = {}
        deadline = time.time() + timeout
        sock.settimeout(timeout)
        while time.time() < deadline:
            try:
                data, _addr = sock.recvfrom(16384)
            except socket.timeout:
                break
            except OSError:
                break
            dev = _sadp_parse(data)
            if dev and dev.ip not in found:
                found[dev.ip] = dev
                if on_result:
                    on_result(dev)
        return list(found.values())
    finally:
        sock.close()


# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────

def devices_to_records(devices: List[DiscoveredDevice], *, username: Optional[str] = None,
                       password: Optional[str] = None, include_meta: bool = True,
                       ) -> List[Dict[str, object]]:
    """Convert cameras into camera-file records (dicts) ready to serialise.

    Only devices that look like cameras are included. ``username``/``password``
    fill the corresponding columns when provided (handy for immediate reuse).
    Discovered metadata (model/serial/firmware/mac/vendor) is included as extra
    columns when ``include_meta`` is set; the loader keeps them in ``Camera.extra``.
    """
    records: List[Dict[str, object]] = []
    for dev in devices:
        if not dev.is_camera:
            continue
        rec: Dict[str, object] = {"ip": dev.ip}
        if dev.port != dev.default_port:
            rec["port"] = dev.port
        if dev.https:
            rec["https"] = True
        if username:
            rec["username"] = username
        if password:
            rec["password"] = password
        if dev.name:
            rec["name"] = dev.name
        if include_meta:
            for key in ("vendor", "model", "serial", "firmware", "mac"):
                value = getattr(dev, key)
                if value:
                    rec[key] = value
        records.append(rec)
    return records
