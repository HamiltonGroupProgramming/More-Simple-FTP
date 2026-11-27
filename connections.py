"""Saved connections for More Simple FTP, shared by the window and the command line.

Host, port, user name and the TLS choice are kept in the settings file (see appsettings).
Passwords never go in that file: when the user asks to remember one it goes to the
operating system's password store (Windows Credential Manager, macOS Keychain, or the
Secret Service on Linux) through the ``keyring`` package. If no store is available the
password simply isn't saved and the user types it when connecting.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Protocol

from appsettings import load_settings, save_settings

KEYRING_SERVICE = "More Simple FTP"


class PasswordStore(Protocol):
    def get(self, name: str) -> Optional[str]: ...
    def set(self, name: str, password: str) -> None: ...
    def delete(self, name: str) -> None: ...


class KeyringStore:
    """Passwords in the system's credential store, one entry per saved connection name."""

    def __init__(self) -> None:
        self._kr = None
        try:
            import keyring
            from keyring.backends import fail
        except Exception:  # not installed, or broken
            return
        try:
            backend = keyring.get_keyring()
        except Exception:
            return
        if isinstance(backend, fail.Keyring):
            return  # no real store on this machine
        self._kr = keyring

    @property
    def available(self) -> bool:
        return self._kr is not None

    def get(self, name: str) -> Optional[str]:
        if not self._kr:
            return None
        try:
            return self._kr.get_password(KEYRING_SERVICE, name)
        except Exception:
            return None

    def set(self, name: str, password: str) -> None:
        if not self._kr:
            raise RuntimeError("No password store is available on this computer.")
        self._kr.set_password(KEYRING_SERVICE, name, password)

    def delete(self, name: str) -> None:
        if not self._kr:
            return
        try:
            self._kr.delete_password(KEYRING_SERVICE, name)
        except Exception:
            pass  # nothing stored


@dataclass
class Connection:
    name: str
    host: str
    port: int = 21
    user: str = "anonymous"
    tls: bool = False
    save_password: bool = False   # True when a password is kept in the password store

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> Optional["Connection"]:
        try:
            name, host = str(d["name"]).strip(), str(d["host"]).strip()
            if not name or not host:
                return None
            return Connection(name=name, host=host, port=int(d.get("port", 21)),
                              user=str(d.get("user", "anonymous")), tls=bool(d.get("tls", False)),
                              save_password=bool(d.get("save_password", False)))
        except (KeyError, TypeError, ValueError):
            return None


def default_name(host: str, user: str) -> str:
    return host if not user or user == "anonymous" else f"{user}@{host}"


class SavedConnections:
    """Load, add, replace and remove saved connections."""

    def __init__(self, settings_file: Optional[str] = None,
                 store: Optional[PasswordStore] = None) -> None:
        self.settings_file = settings_file
        self.store = store if store is not None else KeyringStore()

    @property
    def can_save_passwords(self) -> bool:
        return bool(getattr(self.store, "available", True))

    def _settings(self) -> Dict[str, Any]:
        return load_settings(self.settings_file)

    def all(self) -> List[Connection]:
        raw = self._settings().get("connections", [])
        out = []
        for item in raw if isinstance(raw, list) else []:
            conn = Connection.from_dict(item) if isinstance(item, dict) else None
            if conn and all(c.name.lower() != conn.name.lower() for c in out):
                out.append(conn)
        return out

    def names(self) -> List[str]:
        return [c.name for c in self.all()]

    def get(self, name: str) -> Optional[Connection]:
        key = name.strip().lower()
        return next((c for c in self.all() if c.name.lower() == key), None)

    def password(self, conn: Connection) -> Optional[str]:
        return self.store.get(conn.name) if conn.save_password else None

    def save(self, conn: Connection, password: Optional[str] = None) -> Connection:
        """Add or replace (same name) a connection.

        ``password`` is stored only when ``conn.save_password`` is True; otherwise any
        password previously stored under that name is removed.
        """
        if conn.save_password and password:
            self.store.set(conn.name, password)
        elif not conn.save_password:
            self.store.delete(conn.name)
        settings = self._settings()
        others = [c for c in self.all() if c.name.lower() != conn.name.lower()]
        settings["connections"] = [asdict(c) for c in others + [conn]]
        settings["last_connection"] = conn.name
        save_settings(settings, self.settings_file)
        return conn

    def remove(self, name: str) -> bool:
        conn = self.get(name)
        if conn is None:
            return False
        self.store.delete(conn.name)
        settings = self._settings()
        settings["connections"] = [asdict(c) for c in self.all() if c.name != conn.name]
        if settings.get("last_connection") == conn.name:
            settings.pop("last_connection", None)
        save_settings(settings, self.settings_file)
        return True

    def last_used(self) -> Optional[Connection]:
        name = self._settings().get("last_connection")
        return self.get(name) if isinstance(name, str) else None

    def mark_used(self, name: str) -> None:
        settings = self._settings()
        if settings.get("last_connection") != name:
            settings["last_connection"] = name
            save_settings(settings, self.settings_file)
