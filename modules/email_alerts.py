"""Email / SMTP alerts.

  /ISAPI/System/Network/mailing         SMTP server, sender, receivers (read-modify-write)
  /ISAPI/Event/triggers/VMD-<ch>        "send email" linkage for motion events
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .camera_client import CameraClient, add_children, find, is_ipv4, set_text, text
from .common import add_command, channel_of, int_range, on_off

MAILING_PATH = "/ISAPI/System/Network/mailing"
TRIGGER_PATH = "/ISAPI/Event/triggers/VMD-{ch}"


class EmailAlerts:
    def __init__(self, client: CameraClient):
        self.client = client

    def _mailing(self, root: ET.Element) -> ET.Element:
        mailing = root if root.tag == "mailing" else find(root, "mailing")
        if mailing is None:
            mailing = ET.SubElement(root, "mailing")
            add_children(mailing, {"id": 1})
        return mailing

    def get(self) -> Dict[str, Any]:
        root = self.client.get_xml(MAILING_PATH)
        m = self._mailing(root)
        fmt = text(m, "sender/smtp/addressingFormatType")
        server = text(m, "sender/smtp/hostName") if fmt == "hostname" else text(m, "sender/smtp/ipAddress")
        return {
            "sender_name": text(m, "sender/name"),
            "sender_address": text(m, "sender/emailAddress"),
            "smtp_server": server or text(m, "sender/smtp/hostName") or text(m, "sender/smtp/ipAddress"),
            "smtp_port": text(m, "sender/smtp/portNo"),
            "authentication": text(m, "sender/smtp/enableAuthorization"),
            "smtp_user": text(m, "sender/smtp/accountName"),
            "ssl": text(m, "sender/smtp/enableSSL"),
            "starttls": text(m, "sender/smtp/startTLS"),
            "attach_snapshot": text(m, "attachment/snapshot/enabled"),
            "recipients": [f"{text(r, 'name') or ''} <{text(r, 'emailAddress')}>".strip()
                           for r in m.iter("receiver") if text(r, "emailAddress")],
        }

    def set(self, server=None, port=None, sender=None, sender_name=None, smtp_user=None,
            smtp_password=None, ssl=None, starttls=None, recipients: Optional[List[str]] = None,
            attach_snapshot=None) -> List[str]:
        root = self.client.get_xml(MAILING_PATH)
        m = self._mailing(root)
        changes: List[str] = []
        if server:
            if is_ipv4(server):
                set_text(m, "sender/smtp/addressingFormatType", "ipaddress")
                set_text(m, "sender/smtp/ipAddress", server)
            else:
                set_text(m, "sender/smtp/addressingFormatType", "hostname")
                set_text(m, "sender/smtp/hostName", server)
            changes.append(f"SMTP server {server}")
        if port:
            set_text(m, "sender/smtp/portNo", port)
            changes.append(f"port {port}")
        if sender:
            set_text(m, "sender/emailAddress", sender)
            changes.append(f"sender {sender}")
        if sender_name:
            set_text(m, "sender/name", sender_name)
            changes.append(f"sender name '{sender_name}'")
        if smtp_user is not None:
            set_text(m, "sender/smtp/enableAuthorization", bool(smtp_user))
            set_text(m, "sender/smtp/accountName", smtp_user)
            changes.append(f"SMTP user '{smtp_user}'" if smtp_user else "SMTP auth off")
        if smtp_password:
            set_text(m, "sender/smtp/password", smtp_password)
            changes.append("SMTP password ********")
        if ssl is not None:
            set_text(m, "sender/smtp/enableSSL", ssl)
            changes.append(f"SSL {'on' if ssl else 'off'}")
        if starttls is not None:
            set_text(m, "sender/smtp/startTLS", starttls)
            changes.append(f"STARTTLS {'on' if starttls else 'off'}")
        if attach_snapshot is not None:
            set_text(m, "attachment/snapshot/enabled", attach_snapshot)
            changes.append(f"attach snapshot {'on' if attach_snapshot else 'off'}")
        if recipients is not None:
            rl = find(m, "receiverList")
            if rl is None:
                rl = ET.SubElement(m, "receiverList")
            for r in list(rl):
                rl.remove(r)
            for i, addr in enumerate(recipients, 1):
                add_children(ET.SubElement(rl, "receiver"),
                             {"id": i, "name": addr.split("@")[0], "emailAddress": addr})
            changes.append(f"recipients {', '.join(recipients)}")
        self.client.put_xml(MAILING_PATH, root)
        return changes

    def set_motion_email(self, channel: int, enabled: bool) -> None:
        """Add/remove the 'email' notification on the motion-detection trigger."""
        path = TRIGGER_PATH.format(ch=channel)
        root = self.client.get_xml(path)
        nlist = root.find(".//EventTriggerNotificationList")
        if nlist is None:
            nlist = ET.SubElement(root, "EventTriggerNotificationList")
        existing = [n for n in nlist.findall("EventTriggerNotification")
                    if (text(n, "notificationMethod") or "").lower() == "email"]
        if enabled and not existing:
            add_children(ET.SubElement(nlist, "EventTriggerNotification"),
                         {"id": "email", "notificationMethod": "email",
                          "notificationRecurrence": "beginning"})
        if not enabled:
            for n in existing:
                nlist.remove(n)
        self.client.put_xml(path, root)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def cmd_get_email(client, log, args, camera) -> None:
    alerts = EmailAlerts(client)
    with log.step("retrieve email config"):
        info = alerts.get()
        log.ok("Email config retrieved successfully")
        try:
            root = client.get_xml(TRIGGER_PATH.format(ch=channel_of(args, camera)))
            methods = [(m.text or "").lower() for m in root.iter("notificationMethod")]
            info["email_on_motion"] = "enabled" if "email" in methods else "disabled"
        except Exception:
            pass
        log.data("Email", info, "email")


def cmd_set_email(client, log, args, camera) -> None:
    alerts = EmailAlerts(client)
    smtp_opts = [args.server, args.smtp_port, args.sender, args.sender_name, args.smtp_user,
                 args.smtp_password, args.ssl, args.starttls, args.recipient, args.attach_snapshot]
    if any(v is not None for v in smtp_opts):
        with log.step("set email/SMTP config"):
            changes = alerts.set(args.server, args.smtp_port, args.sender, args.sender_name,
                                 args.smtp_user, args.smtp_password, args.ssl, args.starttls,
                                 args.recipient, args.attach_snapshot)
            log.ok("Email set to: " + ", ".join(changes))
    if args.enable is not None:
        with log.step("set email alert on motion"):
            alerts.set_motion_email(channel_of(args, camera), args.enable)
            log.ok(f"Email alerts on motion {'enabled' if args.enable else 'disabled'}")


def register(sub, parents) -> None:
    p = add_command(sub, "get-email", "Read SMTP/email alert configuration", cmd_get_email, parents)
    p.add_argument("--channel", type=int, help="video channel for the motion linkage (default 1)")

    p = add_command(sub, "set-email", "Configure SMTP server, sender, recipients; enable/disable alerts",
                    cmd_set_email, parents, writes=True,
                    change_opts=["server", "smtp_port", "sender", "sender_name", "smtp_user",
                                 "smtp_password", "ssl", "starttls", "recipient", "attach_snapshot",
                                 "enable"],
                    epilog=("--enable on|off adds/removes the 'send email' action on the motion\n"
                            "detection trigger. --recipient replaces the full receiver list."))
    p.add_argument("--server", help="SMTP server hostname or IP")
    p.add_argument("--smtp-port", type=int_range(1, 65535), help="SMTP port (25, 465, 587…)")
    p.add_argument("--sender", help="sender email address")
    p.add_argument("--sender-name", help="sender display name")
    p.add_argument("--smtp-user", help="SMTP username ('' disables SMTP authentication)")
    p.add_argument("--smtp-password", help="SMTP password")
    p.add_argument("--ssl", type=on_off, metavar="on|off", help="use SSL/TLS")
    p.add_argument("--starttls", type=on_off, metavar="on|off", help="use STARTTLS")
    p.add_argument("--recipient", action="append", metavar="EMAIL",
                   help="recipient address (repeatable, max 3 on most models)")
    p.add_argument("--attach-snapshot", type=on_off, metavar="on|off", help="attach a snapshot")
    p.add_argument("--enable", type=on_off, metavar="on|off", help="email alerts on motion events")
    p.add_argument("--channel", type=int, help="video channel for the motion linkage (default 1)")
