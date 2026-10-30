"""Load camera lists from CSV, YAML or JSON files (auto-detected by extension).

Every entry needs ``ip``. ``username``/``password`` may be omitted in the file
and supplied on the command line instead. Optional fields:

    port      (default 80, or 443 with https)
    https     (true/false, default false)
    auth      (auto | digest | basic)
    name      (friendly label shown in the logs)
    channel   (video channel number, default 1)

Any other columns/keys are kept in ``Camera.extra`` (e.g. ``new_ip`` used by
``set-network``).
"""
from __future__ import annotations

import csv
import ipaddress
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

KNOWN_FIELDS = {"ip", "host", "address", "username", "user", "password", "pass",
                "port", "https", "auth", "name", "channel"}


class CameraFileError(Exception):
    pass


@dataclass
class Camera:
    ip: str
    username: Optional[str] = None
    password: Optional[str] = None
    port: Optional[int] = None
    https: bool = False
    auth: Optional[str] = None
    name: Optional[str] = None
    channel: int = 1
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def effective_port(self) -> int:
        return self.port or (443 if self.https else 80)

    @property
    def label(self) -> str:
        default = 443 if self.https else 80
        host = self.ip if self.effective_port == default else f"{self.ip}:{self.effective_port}"
        return f"{host} [{self.name}]" if self.name else host


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _normalise(entry: Dict[str, Any], where: str) -> Camera:
    if not isinstance(entry, dict):
        raise CameraFileError(f"{where}: expected an object/dict, got {type(entry).__name__}")
    data = {str(k).strip().lower(): (v.strip() if isinstance(v, str) else v)
            for k, v in entry.items() if k is not None}

    ip = data.get("ip") or data.get("host") or data.get("address")
    if _blank(ip):
        raise CameraFileError(f"{where}: missing required field 'ip'")
    ip = str(ip)

    port = data.get("port")
    if not _blank(port):
        try:
            port = int(port)
            if not 1 <= port <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            raise CameraFileError(f"{where}: invalid port {data.get('port')!r}")
    else:
        port = None

    auth = data.get("auth")
    if not _blank(auth):
        auth = str(auth).lower()
        if auth not in ("auto", "digest", "basic"):
            raise CameraFileError(f"{where}: auth must be auto, digest or basic (got {auth!r})")
    else:
        auth = None

    channel = data.get("channel")
    channel = int(channel) if not _blank(channel) else 1

    username = data.get("username", data.get("user"))
    password = data.get("password", data.get("pass"))
    return Camera(
        ip=ip,
        username=None if _blank(username) else str(username),
        password=None if _blank(password) else str(password),
        port=port,
        https=_to_bool(data.get("https", False)) if not _blank(data.get("https")) else False,
        auth=auth,
        name=None if _blank(data.get("name")) else str(data.get("name")),
        channel=channel,
        extra={k: v for k, v in data.items() if k not in KNOWN_FIELDS and not _blank(v)},
    )


def _load_csv(path: str) -> List[Dict[str, Any]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = [line for line in fh if line.strip() and not line.lstrip().startswith("#")]
    reader = csv.DictReader(rows)
    if not reader.fieldnames or "ip" not in [f.strip().lower() for f in reader.fieldnames]:
        raise CameraFileError(f"{path}: CSV needs a header row containing at least 'ip' "
                              "(e.g. ip,username,password,port)")
    return list(reader)


def _unwrap(data: Any, path: str) -> List[Any]:
    """Accept a bare list or a mapping with a 'cameras' key."""
    if isinstance(data, dict) and "cameras" in data:
        data = data["cameras"]
    if not isinstance(data, list):
        raise CameraFileError(f"{path}: expected a list of cameras (or a 'cameras:' key)")
    return data


def _load_yaml(path: str) -> List[Any]:
    try:
        import yaml
    except ImportError:
        raise CameraFileError("PyYAML is required for YAML files: pip install pyyaml")
    with open(path, encoding="utf-8") as fh:
        try:
            return _unwrap(yaml.safe_load(fh) or [], path)
        except yaml.YAMLError as exc:
            raise CameraFileError(f"{path}: invalid YAML: {exc}")


def _load_json(path: str) -> List[Any]:
    with open(path, encoding="utf-8") as fh:
        try:
            return _unwrap(json.load(fh), path)
        except json.JSONDecodeError as exc:
            raise CameraFileError(f"{path}: invalid JSON: {exc}")


LOADERS = {".csv": _load_csv, ".yaml": _load_yaml, ".yml": _load_yaml, ".json": _load_json}


def load_cameras(path: str) -> List[Camera]:
    """Load and validate a camera file. Raises CameraFileError on problems."""
    if not os.path.isfile(path):
        raise CameraFileError(f"Camera file not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    loader = LOADERS.get(ext)
    if loader is None:
        raise CameraFileError(f"Unsupported camera file type '{ext}'. Use .csv, .yaml, .yml or .json")
    entries = loader(path)
    cameras = [_normalise(e, f"{os.path.basename(path)} entry #{i}") for i, e in enumerate(entries, 1)]
    if not cameras:
        raise CameraFileError(f"{path}: no cameras defined")
    seen = set()
    for cam in cameras:
        key = (cam.ip, cam.effective_port)
        if key in seen:
            raise CameraFileError(f"{path}: duplicate camera {cam.ip}:{cam.effective_port}")
        seen.add(key)
    return cameras


def write_camera_file(path: str, records: List[Dict[str, Any]], *,
                      header_comment: Optional[str] = None) -> None:
    """Write camera records to a CSV/YAML/JSON file (format chosen by extension).

    ``records`` is a list of plain dicts (e.g. from
    ``discovery.devices_to_records``). The resulting file can be loaded straight
    back with :func:`load_cameras`.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in LOADERS:
        raise CameraFileError(
            f"Unsupported output type '{ext}'. Use .csv, .yaml, .yml or .json")
    if not records:
        raise CameraFileError("no cameras to write")

    if ext == ".csv":
        # Union of all keys, with the common fields first in a stable order.
        preferred = ["ip", "port", "https", "username", "password", "name",
                     "vendor", "model", "serial", "firmware", "mac"]
        extra = [k for r in records for k in r if k not in preferred]
        seen = set()
        columns = [c for c in preferred if any(c in r for r in records)]
        for k in extra:
            if k not in seen and k not in columns:
                seen.add(k)
                columns.append(k)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            if header_comment:
                fh.write(f"# {header_comment}\n")
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for rec in records:
                writer.writerow({k: _csv_cell(rec.get(k)) for k in columns})
        return

    if ext in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            raise CameraFileError("PyYAML is required to write YAML: pip install pyyaml")
        with open(path, "w", encoding="utf-8") as fh:
            if header_comment:
                fh.write(f"# {header_comment}\n")
            yaml.safe_dump({"cameras": records}, fh, sort_keys=False, default_flow_style=False,
                           allow_unicode=True)
        return

    # JSON
    with open(path, "w", encoding="utf-8") as fh:
        payload: Dict[str, Any] = {}
        if header_comment:
            payload["_comment"] = header_comment
        payload["cameras"] = records
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _csv_cell(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else value


def expand_ip_range(spec: str) -> List[str]:
    """Expand '192.168.1.10-20', '192.168.1.10-192.168.1.20' or a CIDR '192.168.1.0/28'."""
    spec = spec.strip()
    if "/" in spec:
        return [str(h) for h in ipaddress.ip_network(spec, strict=False).hosts()]
    if "-" in spec:
        start, end = spec.split("-", 1)
        start_ip = ipaddress.ip_address(start.strip())
        end = end.strip()
        if "." not in end:
            end = start.rsplit(".", 1)[0] + "." + end
        end_ip = ipaddress.ip_address(end)
        if int(end_ip) < int(start_ip):
            raise ValueError(f"Invalid range {spec}: end is before start")
        return [str(ipaddress.ip_address(i)) for i in range(int(start_ip), int(end_ip) + 1)]
    return [str(ipaddress.ip_address(spec))]
