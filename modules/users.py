"""User accounts (ISAPI /ISAPI/Security/users).

Newer firmware (5.5+) requires the *current* admin password in a
<loginPassword> element when users are modified. Requests are first sent
without it and automatically retried with it if the camera rejects them.
"""
from __future__ import annotations

from getpass import getpass
from typing import Any, Dict, List, Optional

from .camera_client import CameraClient, ISAPIError, add_children, new_element, text
from .common import add_command

USERS_PATH = "/ISAPI/Security/users"
USER_PATH = "/ISAPI/Security/users/{id}"
ROLES = {"admin": "Administrator", "administrator": "Administrator",
         "operator": "Operator", "viewer": "Viewer", "user": "Viewer"}


class Users:
    def __init__(self, client: CameraClient):
        self.client = client

    def list(self) -> List[Dict[str, Any]]:
        root = self.client.get_xml(USERS_PATH)
        return [{"id": text(u, "id"), "username": text(u, "userName"), "role": text(u, "userLevel"),
                 "enabled": text(u, "enabled", "true")} for u in root.iter("User")]

    def find(self, username: str) -> Optional[Dict[str, Any]]:
        for user in self.list():
            if (user["username"] or "").lower() == username.lower():
                return user
        return None

    def _send(self, method: str, path: str, fields: Dict[str, Any]) -> None:
        """Send a <User> document, retrying with <loginPassword> on rejection."""
        def build(with_login: bool):
            root = new_element("User")
            add_children(root, fields)
            if with_login:
                add_children(root, {"loginPassword": self.client.password})
            return root

        try:
            self.client._write(method, path, build(False))
        except ISAPIError as exc:
            if exc.status_code in (400, 403):  # never retry 401 (account lockout risk)
                self.client._write(method, path, build(True))
            else:
                raise

    def add(self, username: str, password: str, role: str = "Operator") -> int:
        users = self.list()
        if any((u["username"] or "").lower() == username.lower() for u in users):
            raise ValueError(f"user '{username}' already exists")
        new_id = max([int(u["id"]) for u in users if (u["id"] or "").isdigit()] + [0]) + 1
        self._send("POST", USERS_PATH, {"id": new_id, "userName": username, "password": password,
                                        "userLevel": role})
        return new_id

    def set_password(self, username: str, new_password: str) -> None:
        user = self.find(username)
        if not user:
            raise ValueError(f"user '{username}' not found")
        self._send("PUT", USER_PATH.format(id=user["id"]),
                   {"id": user["id"], "userName": user["username"], "password": new_password})

    def set_role(self, username: str, role: str) -> None:
        user = self.find(username)
        if not user:
            raise ValueError(f"user '{username}' not found")
        self._send("PUT", USER_PATH.format(id=user["id"]),
                   {"id": user["id"], "userName": user["username"], "userLevel": role})

    def delete(self, username: str) -> None:
        user = self.find(username)
        if not user:
            raise ValueError(f"user '{username}' not found")
        if user["id"] == "1" or (user["username"] or "").lower() == "admin":
            raise ValueError("refusing to delete the built-in admin account")
        self.client.delete(USER_PATH.format(id=user["id"]))


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def cmd_list_users(client, log, args, camera) -> None:
    with log.step("list users"):
        users = Users(client).list()
        log.ok(f"Retrieved {len(users)} user account(s)")
        log.data("Users", [f"#{u['id']}  {u['username']}  ({u['role']})" for u in users], "users")


def cmd_add_user(client, log, args, camera) -> None:
    with log.step(f"add user '{args.user}'"):
        new_id = Users(client).add(args.user, args.new_password, ROLES[args.role])
        log.ok(f"User '{args.user}' added as {ROLES[args.role]} (id {new_id})")


def cmd_set_password(client, log, args, camera) -> None:
    target = args.user or camera.username
    with log.step(f"change password for '{target}'"):
        Users(client).set_password(target, args.new_password)
        log.ok(f"Password changed for user '{target}'")
        if target == camera.username:
            log.warn("This is the login account - update the password in your camera file")


def cmd_set_role(client, log, args, camera) -> None:
    with log.step(f"change role for '{args.user}'"):
        Users(client).set_role(args.user, ROLES[args.role])
        log.ok(f"User '{args.user}' role set to: {ROLES[args.role]}")


def cmd_delete_user(client, log, args, camera) -> None:
    with log.step(f"delete user '{args.user}'"):
        if camera.username and args.user.lower() == camera.username.lower():
            raise ValueError("refusing to delete the account used to log in")
        Users(client).delete(args.user)
        log.ok(f"User '{args.user}' deleted")


def _prompt_new_password(args) -> None:
    if not args.new_password:
        first = getpass(f"New password for '{args.user or 'login user'}': ")
        if first != getpass("Repeat new password: "):
            raise SystemExit("Passwords do not match")
        args.new_password = first
    if len(args.new_password) < 8:
        raise SystemExit("Hikvision requires passwords of at least 8 characters")


def register(sub, parents) -> None:
    add_command(sub, "list-users", "List all user accounts on the device", cmd_list_users, parents)

    p = add_command(sub, "add-user", "Add a user account with a role", cmd_add_user, parents,
                    writes=True)
    p.add_argument("--user", required=True, help="new user name")
    p.add_argument("--new-password", help="password for the new user (prompted if omitted)")
    p.add_argument("--role", choices=["administrator", "operator", "viewer"], default="operator",
                   help="user level (default operator)")
    p.set_defaults(_post_parse=_prompt_new_password)

    p = add_command(sub, "set-password", "Change a user's password", cmd_set_password, parents,
                    writes=True)
    p.add_argument("--user", help="account to change (default: the login user of each camera)")
    p.add_argument("--new-password", help="new password (prompted if omitted - recommended)")
    p.set_defaults(_post_parse=_prompt_new_password)

    p = add_command(sub, "set-user-role", "Change a user's role", cmd_set_role, parents, writes=True)
    p.add_argument("--user", required=True, help="account to change")
    p.add_argument("--role", choices=["administrator", "operator", "viewer"], required=True)

    p = add_command(sub, "delete-user", "Delete a user account", cmd_delete_user, parents,
                    writes=True)
    p.add_argument("--user", required=True, help="account to delete")
