"""Network settings (ISAPI /ISAPI/System/Network/interfaces/<id>/ipAddress).

Changes are applied with read-modify-write: the current <IPAddress> document
is fetched, only the requested fields are changed, and it is PUT back.

⚠ Changing the IP address or switching to DHCP will drop the connection to the
camera; the new address must be reachable from this machine to manage it later.
"""
from __future__ import annotations

from typing import Any, Dict

from .camera_client import CameraClient, find, is_ipv4, set_text, text
from .common import add_command

IFACE_PATH = "/ISAPI/System/Network/interfaces/{id}"
IP_PATH = "/ISAPI/System/Network/interfaces/{id}/ipAddress"


class Network:
    def __init__(self, client: CameraClient, interface: int = 1):
        self.client = client
        self.interface = interface

    def get(self) -> Dict[str, Any]:
        root = self.client.get_xml(IP_PATH.format(id=self.interface))
        addressing = text(root, "addressingType")
        result = {
            "interface": self.interface,
            "addressing": addressing,
            "dhcp": "enabled" if (addressing or "").lower() == "dynamic" else "disabled",
            "ip_address": text(root, "ipAddress"),
            "subnet_mask": text(root, "subnetMask"),
            "gateway": text(root, "DefaultGateway/ipAddress"),
            "primary_dns": text(root, "PrimaryDNS/ipAddress"),
            "secondary_dns": text(root, "SecondaryDNS/ipAddress"),
        }
        try:  # MAC/MTU live on the parent interface resource
            iface = self.client.get_xml(IFACE_PATH.format(id=self.interface))
            result["mac_address"] = text(iface, "Link/MACAddress")
            result["mtu"] = text(iface, "Link/MTU")
        except Exception:
            pass
        return result

    def set(self, dhcp=None, ip=None, netmask=None, gateway=None, dns1=None, dns2=None) -> Dict[str, str]:
        root = self.client.get_xml(IP_PATH.format(id=self.interface))
        changes: Dict[str, str] = {}
        if dhcp is not None:
            set_text(root, "addressingType", "dynamic" if dhcp else "static")
            changes["addressing"] = "DHCP" if dhcp else "static"
        if ip:
            set_text(root, "ipAddress", ip)
            changes["ip_address"] = ip
        if netmask:
            set_text(root, "subnetMask", netmask)
            changes["subnet_mask"] = netmask
        if gateway:
            set_text(root, "DefaultGateway/ipAddress", gateway)
            changes["gateway"] = gateway
        if dns1:
            set_text(root, "PrimaryDNS/ipAddress", dns1)
            changes["primary_dns"] = dns1
        if dns2:
            set_text(root, "SecondaryDNS/ipAddress", dns2)
            changes["secondary_dns"] = dns2
        if find(root, "ipVersion") is None:
            set_text(root, "ipVersion", "v4")
        self.client.put_xml(IP_PATH.format(id=self.interface), root)
        return changes


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def cmd_get_network(client, log, args, camera) -> None:
    with log.step("retrieve network config"):
        info = Network(client, args.interface).get()
        log.ok("Network config retrieved successfully")
        log.data("Network", info, "network")


def cmd_set_network(client, log, args, camera) -> None:
    new_ip = args.new_ip or camera.extra.get("new_ip")
    netmask = args.netmask or camera.extra.get("new_netmask")
    gateway = args.gateway or camera.extra.get("new_gateway")
    dhcp = True if args.dhcp else (False if (args.static or new_ip) else None)

    with log.step("validate network settings") as s:
        for label, value in (("IP", new_ip), ("netmask", netmask), ("gateway", gateway),
                             ("DNS 1", args.dns1), ("DNS 2", args.dns2)):
            if value and not is_ipv4(str(value)):
                raise ValueError(f"invalid {label} address {value!r}")
        if dhcp and new_ip:
            raise ValueError("cannot combine --dhcp with a static IP address")
        if not any([dhcp is not None, new_ip, netmask, gateway, args.dns1, args.dns2]):
            raise ValueError("nothing to change for this camera")
    if not s.ok:
        return

    with log.step("set network config"):
        changes = Network(client, args.interface).set(dhcp, new_ip, netmask, gateway,
                                                      args.dns1, args.dns2)
        for key, value in changes.items():
            log.ok(f"Network {key.replace('_', ' ')} set to: {value}")
        if dhcp or (new_ip and new_ip != camera.ip):
            log.warn("Address change applied - the camera will be reachable at its new address "
                     "(a reboot may be required on some models)")


def register(sub, parents) -> None:
    p = add_command(sub, "get-network", "Read IP address, subnet, gateway, DNS and DHCP status",
                    cmd_get_network, parents)
    p.add_argument("--interface", type=int, default=1, help="network interface id (default 1)")

    p = add_command(sub, "set-network", "Set static IP or DHCP, gateway and DNS servers",
                    cmd_set_network, parents, writes=True,  # validated per camera (new_ip field)
                    epilog=("Per-camera static IPs: add a 'new_ip' (and optionally 'new_netmask',\n"
                            "'new_gateway') column/key to the camera file. --new-ip may only be used\n"
                            "with a single camera."))
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dhcp", action="store_true", help="switch to DHCP addressing")
    mode.add_argument("--static", action="store_true", help="switch to static addressing")
    p.add_argument("--new-ip", help="new static IP address (single camera only)")
    p.add_argument("--netmask", help="subnet mask, e.g. 255.255.255.0")
    p.add_argument("--gateway", help="default gateway")
    p.add_argument("--dns1", help="primary DNS server")
    p.add_argument("--dns2", help="secondary DNS server")
    p.add_argument("--interface", type=int, default=1, help="network interface id (default 1)")
    p.set_defaults(single_camera_opts=["new_ip"])
