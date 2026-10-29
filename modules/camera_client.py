"""Base Hikvision ISAPI client: authentication, HTTP calls and XML helpers.

All responses are parsed into ``xml.etree.ElementTree`` elements with the XML
namespace stripped from every tag, so module code can use simple paths such as
``"Video/videoCodecType"``. The original default namespace is preserved as an
``xmlns`` attribute on the root element, so a modified tree can be PUT back to
the camera unchanged ("read-modify-write"), which is the most firmware-tolerant
way of changing settings.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

try:  # Safe XML parsing (protects against XML bombs / entity expansion).
    from defusedxml.ElementTree import fromstring as _xml_fromstring
except ImportError:  # pragma: no cover - fallback if defusedxml is missing
    from xml.etree.ElementTree import fromstring as _xml_fromstring

HIK_NS_V20 = "http://www.hikvision.com/ver20/XMLSchema"
XML_HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'


# ─────────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────────

class ISAPIError(Exception):
    """Raised when the camera returns an HTTP error or cannot be reached."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def _describe_response_error(resp: requests.Response) -> str:
    """Build e.g. '403 Forbidden (Invalid Operation / notSupport)'."""
    msg = f"{resp.status_code} {resp.reason or ''}".strip()
    try:
        root = parse_xml(resp.text)
        seen = {(resp.reason or "").lower()}
        details = []
        for t in (text(root, "statusString"), text(root, "subStatusCode")):
            if t and t.lower() not in seen:
                seen.add(t.lower())
                details.append(t)
        if details:
            msg += f" ({' / '.join(details)})"
    except Exception:
        pass
    return msg


def _describe_request_exception(exc: requests.RequestException) -> str:
    if isinstance(exc, requests.Timeout):
        return "Connection timed out"
    if isinstance(exc, requests.exceptions.SSLError):
        return "SSL/TLS error"
    if isinstance(exc, requests.ConnectionError):
        text_ = str(exc)
        if "refused" in text_.lower():
            return "Connection refused"
        if "No route to host" in text_:
            return "No route to host"
        return "Connection error (camera unreachable)"
    return f"{exc.__class__.__name__}: {exc}"


# ─────────────────────────────────────────────────────────────────────────────
# XML helpers (namespace-agnostic)
# ─────────────────────────────────────────────────────────────────────────────

def parse_xml(xml_text: str) -> ET.Element:
    """Parse XML, strip namespaces from all tags, keep root namespace as xmlns."""
    root = _xml_fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag[1:].split("}", 1)[0]
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.startswith("{"):
            el.tag = el.tag.split("}", 1)[1]
        for key in list(el.attrib):
            if key.startswith("{"):
                el.attrib[key.split("}", 1)[1]] = el.attrib.pop(key)
    if ns:
        root.set("xmlns", ns)
    return root


def to_xml(root: ET.Element) -> str:
    """Serialize an element (as produced by parse_xml/new_element) to a string."""
    return XML_HEADER + ET.tostring(root, encoding="unicode")


def new_element(tag: str, namespace: Optional[str] = HIK_NS_V20, version: str = "2.0") -> ET.Element:
    """Create a new root element with Hikvision's default namespace."""
    root = ET.Element(tag)
    if version:
        root.set("version", version)
    if namespace:
        root.set("xmlns", namespace)
    return root


def find(root: Optional[ET.Element], path: str) -> Optional[ET.Element]:
    """Find a child by slash path ('a/b/c'). Returns None if any part is missing."""
    if root is None:
        return None
    return root.find(path)


def find_any(root: Optional[ET.Element], tag: str) -> Optional[ET.Element]:
    """Find the first descendant with the given tag anywhere in the tree."""
    if root is None:
        return None
    return root.find(f".//{tag}")


def text(root: Optional[ET.Element], path: str, default: Optional[str] = None) -> Optional[str]:
    el = find(root, path)
    if el is None or el.text is None:
        return default
    return el.text.strip()


def set_text(root: ET.Element, path: str, value: Any, create: bool = True) -> ET.Element:
    """Set the text of the element at ``path``, creating missing parents if needed."""
    current = root
    for part in path.split("/"):
        child = current.find(part)
        if child is None:
            if not create:
                raise KeyError(f"Element '{path}' not found")
            child = ET.SubElement(current, part)
        current = child
    if isinstance(value, bool):
        value = "true" if value else "false"
    current.text = str(value)
    return current


def add_children(parent: ET.Element, values: Dict[str, Any]) -> ET.Element:
    """Append simple child elements from a dict (nested dicts become sub-elements)."""
    for key, value in values.items():
        if value is None:
            continue
        child = ET.SubElement(parent, key)
        if isinstance(value, dict):
            add_children(child, value)
        elif isinstance(value, bool):
            child.text = "true" if value else "false"
        else:
            child.text = str(value)
    return parent


def element_to_dict(el: ET.Element) -> Any:
    """Convert an element tree into plain dicts/lists (repeated tags -> list)."""
    children = list(el)
    if not children:
        return (el.text or "").strip()
    result: Dict[str, Any] = {}
    for child in children:
        value = element_to_dict(child)
        if child.tag in result:
            if not isinstance(result[child.tag], list):
                result[child.tag] = [result[child.tag]]
            result[child.tag].append(value)
        else:
            result[child.tag] = value
    return result


def as_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    return value.strip().lower() in ("true", "1", "yes", "on")


def xml_escape(value: str) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def is_ipv4(value: str) -> bool:
    return bool(IPV4_RE.match(value or "")) and all(0 <= int(p) <= 255 for p in value.split("."))


# ─────────────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────────────

class CameraClient:
    """HTTP client for a single camera's ISAPI interface.

    auth_mode:
        "auto"   - try Digest first; fall back to Basic if the camera only offers Basic.
        "digest" - Digest only (default for Hikvision firmware since ~2016).
        "basic"  - Basic only (older firmware / when explicitly enabled).
    """

    def __init__(self, ip: str, username: str, password: str, port: int = 80,
                 use_https: bool = False, timeout: float = 5.0, auth_mode: str = "auto",
                 verify_tls: bool = False, dry_run: bool = False):
        self.ip = ip
        self.username = username
        self.password = password
        self.port = int(port)
        self.use_https = use_https
        self.timeout = timeout
        self.auth_mode = auth_mode
        self.dry_run = dry_run
        scheme = "https" if use_https else "http"
        default_port = 443 if use_https else 80
        host = ip if self.port == default_port else f"{ip}:{self.port}"
        self.base_url = f"{scheme}://{host}"
        self.session = requests.Session()
        self.session.verify = verify_tls
        self.session.headers.update({"User-Agent": "hik-config/2.0"})
        self._auth = self._make_auth("basic" if auth_mode == "basic" else "digest")
        self._auth_failed = False  # set after a 401 so we stop retrying (account lockout)
        self.unreachable = False   # set after a connection error / timeout
        if not verify_tls:
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:  # pragma: no cover
                pass

    def _make_auth(self, kind: str):
        if kind == "basic":
            return HTTPBasicAuth(self.username, self.password)
        return HTTPDigestAuth(self.username, self.password)

    @property
    def halted(self) -> bool:
        """True when further requests are pointless (bad credentials or camera offline)."""
        return self._auth_failed or self.unreachable

    def close(self) -> None:
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── low level ────────────────────────────────────────────────────────────
    def request(self, method: str, path: str, data: Optional[str] = None,
                params: Optional[Dict[str, Any]] = None, raw: bool = False,
                ok_statuses=(200, 201, 204)) -> requests.Response:
        """Perform an HTTP request and raise ISAPIError on failure.

        Write requests (PUT/POST/DELETE) are skipped when dry_run is enabled.
        """
        method = method.upper()
        if self._auth_failed:
            raise ISAPIError("skipped - authentication failed earlier (not retrying, to avoid "
                             "locking the account)", 401)
        if self.dry_run and method in ("PUT", "POST", "DELETE"):
            fake = requests.Response()
            fake.status_code = 200
            fake._content = b""
            return fake

        url = self.base_url + (path if path.startswith("/") else "/" + path)
        headers = {"Content-Type": "application/xml; charset=UTF-8"} if data is not None else {}
        body = data.encode("utf-8") if isinstance(data, str) else data
        try:
            resp = self.session.request(method, url, data=body, params=params, headers=headers,
                                        auth=self._auth, timeout=self.timeout)
            # Auto mode: camera only offers Basic auth -> switch and retry once.
            if (resp.status_code == 401 and self.auth_mode == "auto"
                    and isinstance(self._auth, HTTPDigestAuth)):
                challenge = resp.headers.get("WWW-Authenticate", "")
                if "basic" in challenge.lower() and "digest" not in challenge.lower():
                    self._auth = self._make_auth("basic")
                    resp = self.session.request(method, url, data=body, params=params,
                                                headers=headers, auth=self._auth,
                                                timeout=self.timeout)
        except requests.RequestException as exc:
            if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
                self.unreachable = True
            raise ISAPIError(_describe_request_exception(exc)) from exc

        if resp.status_code == 401:
            self._auth_failed = True
            raise ISAPIError(_describe_response_error(resp) + " - check username/password", 401)
        if resp.status_code not in ok_statuses:
            raise ISAPIError(_describe_response_error(resp), resp.status_code)
        return resp

    # ── XML convenience ──────────────────────────────────────────────────────
    def get_xml(self, path: str, params: Optional[Dict[str, Any]] = None) -> ET.Element:
        resp = self.request("GET", path, params=params)
        try:
            return parse_xml(resp.text)
        except Exception as exc:
            raise ISAPIError(f"Invalid XML response from {path}: {exc}") from exc

    def _write(self, method: str, path: str, body, params=None) -> Optional[ET.Element]:
        data = to_xml(body) if isinstance(body, ET.Element) else body
        resp = self.request(method, path, data=data, params=params)
        if resp.text and resp.text.lstrip().startswith("<"):
            try:
                return parse_xml(resp.text)
            except Exception:
                return None
        return None

    def put_xml(self, path: str, body=None, params=None) -> Optional[ET.Element]:
        return self._write("PUT", path, body, params)

    def post_xml(self, path: str, body=None, params=None) -> Optional[ET.Element]:
        return self._write("POST", path, body, params)

    def delete(self, path: str, params=None) -> None:
        self.request("DELETE", path, params=params)

    def __repr__(self) -> str:
        return f"CameraClient({self.base_url}, user={self.username!r})"
