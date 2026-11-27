#!/usr/bin/env python3
"""More Simple FTP - a small, modern two-pane FTP / FTPS client.

Local files on the left, the server on the right. Drag files between the
panes, or use the Upload / Download buttons. Connection details can be saved
by name; passwords are optionally kept in the operating system's credential
store (Windows Credential Manager on Windows) and never in the settings file.

Only the Python standard library is required. The optional ``keyring``
package enables saved passwords.
"""

from __future__ import annotations

import datetime as _dt
import ftplib
import json
import os
import posixpath
import queue
import shutil
import socket
import ssl
import stat
import string
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

try:  # optional: saved passwords
    import keyring  # type: ignore
    from keyring.errors import PasswordDeleteError  # type: ignore
except Exception:  # pragma: no cover - depends on environment
    keyring = None
    PasswordDeleteError = Exception

APP_NAME = "More Simple FTP"
APP_VERSION = "0.1.0"
KEYRING_SERVICE = "MoreSimpleFTP"
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".moresimpleftp.json")
IS_WINDOWS = sys.platform.startswith("win")
THIS_PC = ""  # pseudo-path for the Windows drive list

# --------------------------------------------------------------------------
# Settings and saved servers
# --------------------------------------------------------------------------

DEFAULT_SERVER = {
    "name": "",
    "host": "",
    "port": 21,
    "user": "",
    "tls": False,
    "passive": True,
    "remote_dir": "",
    "save_password": False,
}


class Settings:
    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self.data = {"theme": "system", "servers": [], "last_server": "",
                     "log_open": False, "local_dir": ""}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except FileNotFoundError:
            pass
        except Exception:
            # A damaged settings file should never stop the app starting.
            pass
        clean = []
        for s in self.data.get("servers", []):
            if isinstance(s, dict) and s.get("name"):
                merged = dict(DEFAULT_SERVER)
                merged.update({k: s[k] for k in DEFAULT_SERVER if k in s})
                clean.append(merged)
        self.data["servers"] = clean

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2)
        os.replace(tmp, self.path)

    # servers -------------------------------------------------------------
    @property
    def servers(self) -> list:
        return self.data["servers"]

    def get_server(self, name: str):
        for s in self.servers:
            if s["name"] == name:
                return s
        return None

    def put_server(self, server: dict, old_name: str | None = None) -> None:
        target = old_name or server["name"]
        for i, s in enumerate(self.servers):
            if s["name"] == target:
                self.servers[i] = server
                break
        else:
            self.servers.append(server)
        self.servers.sort(key=lambda s: s["name"].lower())
        self.save()

    def delete_server(self, name: str) -> None:
        self.data["servers"] = [s for s in self.servers if s["name"] != name]
        if self.data.get("last_server") == name:
            self.data["last_server"] = ""
        self.save()


def password_store_available() -> bool:
    return keyring is not None


def get_saved_password(name: str) -> str:
    if keyring is None or not name:
        return ""
    try:
        return keyring.get_password(KEYRING_SERVICE, name) or ""
    except Exception:
        return ""


def set_saved_password(name: str, password: str) -> bool:
    if keyring is None or not name:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, name, password)
        return True
    except Exception:
        return False


def delete_saved_password(name: str) -> None:
    if keyring is None or not name:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, name)
    except (PasswordDeleteError, Exception):
        pass


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def human_size(n) -> str:
    if n is None:
        return ""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def human_time(ts) -> str:
    if ts is None:
        return ""
    try:
        return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


class Entry:
    """One row in a file list."""

    __slots__ = ("name", "is_dir", "size", "mtime")

    def __init__(self, name, is_dir, size=None, mtime=None):
        self.name = name
        self.is_dir = is_dir
        self.size = size
        self.mtime = mtime


def sort_entries(entries):
    return sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))


def list_local(path: str):
    if IS_WINDOWS and path == THIS_PC:
        drives = []
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.exists(root):
                drives.append(Entry(root, True))
        return drives
    out = []
    with os.scandir(path) as it:
        for de in it:
            try:
                st = de.stat()
                is_dir = stat.S_ISDIR(st.st_mode)
                out.append(Entry(de.name, is_dir, None if is_dir else st.st_size,
                                 st.st_mtime))
            except OSError:
                continue
    return sort_entries(out)


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
     "nov", "dec"], start=1)}


def parse_list_line(line: str):
    """Parse a Unix-style or DOS-style LIST line. Returns an Entry or None."""
    parts = line.split(None, 8)
    if len(parts) >= 9 and parts[0][:1] in "-dlbcps":
        perms, size, mon, day, yt, name = (parts[0], parts[4], parts[5],
                                           parts[6], parts[7], parts[8])
        is_dir = perms.startswith("d")
        if perms.startswith("l") and " -> " in name:
            name = name.split(" -> ", 1)[0]
            is_dir = True  # treat links as folders; opening will tell
        mtime = None
        try:
            month = _MONTHS[mon[:3].lower()]
            if ":" in yt:
                now = _dt.datetime.now()
                hh, mm = yt.split(":")
                d = _dt.datetime(now.year, month, int(day), int(hh), int(mm))
                if d > now + _dt.timedelta(days=2):
                    d = d.replace(year=now.year - 1)
            else:
                d = _dt.datetime(int(yt), month, int(day))
            mtime = d.timestamp()
        except Exception:
            pass
        try:
            sz = None if is_dir else int(size)
        except ValueError:
            sz = None
        return Entry(name, is_dir, sz, mtime)
    # DOS / IIS: 01-31-24  10:15AM  <DIR>  name
    parts = line.split(None, 3)
    if len(parts) == 4 and "-" in parts[0]:
        is_dir = parts[2].upper() == "<DIR>"
        mtime = None
        try:
            mtime = _dt.datetime.strptime(f"{parts[0]} {parts[1]}",
                                          "%m-%d-%y %I:%M%p").timestamp()
        except ValueError:
            pass
        sz = None
        if not is_dir:
            try:
                sz = int(parts[2])
            except ValueError:
                pass
        return Entry(parts[3], is_dir, sz, mtime)
    return None


def parse_mlsd_time(value: str):
    """MLSD 'modify' facts are UTC: YYYYMMDDHHMMSS[.sss]."""
    try:
        d = _dt.datetime.strptime(value[:14], "%Y%m%d%H%M%S")
        return d.replace(tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return None


class CancelledError(Exception):
    pass


# --------------------------------------------------------------------------
# FTP worker (all network I/O happens on one background thread)
# --------------------------------------------------------------------------

class ReusingFTP_TLS(ftplib.FTP_TLS):
    """FTP_TLS that reuses the control connection's TLS session on data
    connections. Many servers (vsftpd, FileZilla Server) require this."""

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host,
                                            session=self.sock.session)
        return conn, size


class FTPWorker(threading.Thread):
    def __init__(self, post):
        super().__init__(daemon=True)
        self.post = post  # callable(kind, payload) -> delivered on UI thread
        self.jobs: queue.Queue = queue.Queue()
        self.ftp: ftplib.FTP | None = None
        self.params: dict | None = None
        # Cancelling bumps the generation; a transfer stops when the
        # generation it was queued under is no longer current.
        self.generation = 0
        self.job_generation = 0
        self.pending_transfers = 0
        self.lock = threading.Lock()
        self.mlsd_ok = True

    # -- job submission (UI thread) -------------------------------------
    def submit(self, kind, transfer=False, **kw):
        if transfer:
            with self.lock:
                self.pending_transfers += 1
        self.jobs.put((kind, transfer, kw, self.generation))

    def cancel_transfers(self):
        self.generation += 1
        drained = []
        try:
            while True:
                job = self.jobs.get_nowait()
                if job[1]:
                    with self.lock:
                        self.pending_transfers -= 1
                else:
                    drained.append(job)
        except queue.Empty:
            pass
        for job in drained:
            self.jobs.put(job)

    def queued(self) -> int:
        with self.lock:
            return self.pending_transfers

    # -- thread body -----------------------------------------------------
    def run(self):
        while True:
            kind, transfer, kw, gen = self.jobs.get()
            self.job_generation = gen
            if kind == "quit":
                self._close()
                return
            try:
                getattr(self, "do_" + kind)(**kw)
            except CancelledError:
                self.post("log", ("warn", "Transfer cancelled."))
                self._recover()
            except ssl.SSLCertVerificationError as e:
                self.post("cert_error", {"error": str(e), "params": kw})
                self._close()
            except (ftplib.all_errors) as e:
                self.post("error", {"job": kind, "message": str(e) or e.__class__.__name__})
                if isinstance(e, (OSError, EOFError)) and kind != "connect":
                    self._close()
                    self.post("disconnected", {"unexpected": True})
            except Exception as e:  # keep the worker alive whatever happens
                self.post("error", {"job": kind, "message": f"{e.__class__.__name__}: {e}"})
            finally:
                if transfer:
                    with self.lock:
                        self.pending_transfers = max(0, self.pending_transfers - 1)
                        left = self.pending_transfers
                    self.post("queue", left)

    # -- connection ------------------------------------------------------
    def _close(self):
        if self.ftp is not None:
            try:
                self.ftp.quit()
            except Exception:
                try:
                    self.ftp.close()
                except Exception:
                    pass
        self.ftp = None

    def _open(self, p: dict):
        if p.get("tls"):
            if p.get("verify", True):
                ctx = ssl.create_default_context()
            else:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            ftp = ReusingFTP_TLS(context=ctx, timeout=30)
        else:
            ftp = ftplib.FTP(timeout=30)
        ftp.encoding = "utf-8"
        welcome = ftp.connect(p["host"], int(p.get("port") or 21))
        if p.get("tls"):
            ftp.auth()
        ftp.login(p.get("user") or "anonymous", p.get("password") or "")
        if p.get("tls"):
            ftp.prot_p()
        ftp.set_pasv(bool(p.get("passive", True)))
        try:
            ftp.sendcmd("OPTS UTF8 ON")
        except ftplib.all_errors:
            pass
        return ftp, welcome

    def _recover(self):
        """After an aborted transfer the control connection is in an unknown
        state; simply reconnect and return to the same folder."""
        cwd = None
        try:
            cwd = self.ftp.pwd() if self.ftp else None
        except Exception:
            pass
        self._close()
        if self.params:
            try:
                self.ftp, _ = self._open(self.params)
                if cwd:
                    self.ftp.cwd(cwd)
                self.post("log", ("info", "Reconnected."))
            except Exception as e:
                self.post("error", {"job": "connect", "message": str(e)})
                self.post("disconnected", {"unexpected": True})

    def do_connect(self, **p):
        self._close()
        self.post("log", ("info", f"Connecting to {p['host']}:{p.get('port') or 21}"
                          + (" (FTPS)" if p.get("tls") else "") + " ..."))
        self.ftp, welcome = self._open(p)
        self.params = p
        self.mlsd_ok = True
        for line in (welcome or "").splitlines():
            self.post("log", ("server", line))
        start = p.get("remote_dir") or ""
        if start:
            try:
                self.ftp.cwd(start)
            except ftplib.error_perm as e:
                self.post("log", ("warn", f"Start folder not available: {e}"))
        self.post("connected", {"host": p["host"], "tls": bool(p.get("tls"))})
        self.do_list(path=None)

    def do_disconnect(self):
        self._close()
        self.params = None
        self.post("disconnected", {"unexpected": False})

    def _need(self):
        if self.ftp is None:
            raise ftplib.error_temp("Not connected")
        return self.ftp

    # -- listing ---------------------------------------------------------
    def _listdir(self, ftp):
        entries = []
        if self.mlsd_ok:
            try:
                for name, facts in ftp.mlsd(facts=["type", "size", "modify"]):
                    t = facts.get("type", "").lower()
                    if t in ("cdir", "pdir") or name in (".", ".."):
                        continue
                    is_dir = t == "dir" or t.startswith("os.unix=slink")
                    size = None
                    if not is_dir and facts.get("size", "").isdigit():
                        size = int(facts["size"])
                    mtime = parse_mlsd_time(facts["modify"]) if "modify" in facts else None
                    entries.append(Entry(name, is_dir, size, mtime))
                return sort_entries(entries)
            except ftplib.error_perm:
                self.mlsd_ok = False
        lines = []
        ftp.retrlines("LIST", lines.append)
        for line in lines:
            e = parse_list_line(line)
            if e and e.name not in (".", ".."):
                entries.append(e)
        return sort_entries(entries)

    def do_list(self, path=None):
        ftp = self._need()
        if path:
            ftp.cwd(path)
        cwd = ftp.pwd()
        entries = self._listdir(ftp)
        self.post("listing", {"path": cwd, "entries": entries})

    def do_mkdir(self, name):
        ftp = self._need()
        ftp.mkd(name)
        self.post("log", ("ok", f"Created folder {name}"))
        self.do_list()

    def do_rename(self, old, new):
        ftp = self._need()
        ftp.rename(old, new)
        self.post("log", ("ok", f"Renamed {old} to {new}"))
        self.do_list()

    def _rmtree(self, ftp, name):
        here = ftp.pwd()
        ftp.cwd(name)
        try:
            for e in self._listdir(ftp):
                if e.is_dir:
                    self._rmtree(ftp, e.name)
                else:
                    ftp.delete(e.name)
        finally:
            ftp.cwd(here)
        ftp.rmd(name)

    def do_delete(self, items):
        ftp = self._need()
        for name, is_dir in items:
            if is_dir:
                self._rmtree(ftp, name)
            else:
                ftp.delete(name)
            self.post("log", ("ok", f"Deleted {name}"))
        self.do_list()

    # -- transfers -------------------------------------------------------
    def _progress(self, label, total):
        state = {"done": 0, "start": time.monotonic(), "last": 0.0}

        def cb(n):
            if self.job_generation != self.generation:
                raise CancelledError()
            state["done"] += n
            now = time.monotonic()
            if now - state["last"] >= 0.1:
                state["last"] = now
                elapsed = max(now - state["start"], 1e-6)
                self.post("progress", {"label": label, "done": state["done"],
                                       "total": total, "speed": state["done"] / elapsed})
        return cb, state

    def _finish(self, label, state):
        elapsed = max(time.monotonic() - state["start"], 1e-6)
        self.post("progress", {"label": label, "done": state["done"],
                               "total": state["done"], "speed": state["done"] / elapsed,
                               "finished": True})

    def _download_file(self, ftp, remote_name, local_path):
        total = None
        try:
            ftp.voidcmd("TYPE I")
            total = ftp.size(remote_name)
        except ftplib.all_errors:
            pass
        label = f"Downloading {remote_name}"
        cb, state = self._progress(label, total)
        tmp = local_path + ".part"
        try:
            with open(tmp, "wb") as fh:
                def write(block):
                    fh.write(block)
                    cb(len(block))
                ftp.retrbinary("RETR " + remote_name, write, blocksize=65536)
            os.replace(tmp, local_path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        self._finish(label, state)
        self.post("log", ("ok", f"Downloaded {remote_name} ({human_size(state['done'])})"))

    def _download_tree(self, ftp, name, local_dir):
        target = os.path.join(local_dir, name)
        os.makedirs(target, exist_ok=True)
        here = ftp.pwd()
        ftp.cwd(name)
        try:
            for e in self._listdir(ftp):
                if e.is_dir:
                    self._download_tree(ftp, e.name, target)
                else:
                    self._download_file(ftp, e.name, os.path.join(target, e.name))
        finally:
            ftp.cwd(here)

    def do_download(self, name, is_dir, local_dir, remote_dir):
        ftp = self._need()
        if ftp.pwd() != remote_dir:
            ftp.cwd(remote_dir)
        if is_dir:
            self._download_tree(ftp, name, local_dir)
        else:
            self._download_file(ftp, name, os.path.join(local_dir, name))
        self.post("local_changed", {"path": local_dir})

    def _upload_file(self, ftp, local_path, remote_name):
        total = os.path.getsize(local_path)
        label = f"Uploading {remote_name}"
        cb, state = self._progress(label, total)
        with open(local_path, "rb") as fh:
            ftp.storbinary("STOR " + remote_name, fh, blocksize=65536,
                           callback=lambda b: cb(len(b)))
        self._finish(label, state)
        self.post("log", ("ok", f"Uploaded {remote_name} ({human_size(total)})"))

    def _upload_tree(self, ftp, local_path):
        name = os.path.basename(local_path.rstrip("\\/"))
        try:
            ftp.mkd(name)
        except ftplib.error_perm:
            pass  # probably exists already
        here = ftp.pwd()
        ftp.cwd(name)
        try:
            for e in list_local(local_path):
                p = os.path.join(local_path, e.name)
                if e.is_dir:
                    self._upload_tree(ftp, p)
                else:
                    self._upload_file(ftp, p, e.name)
        finally:
            ftp.cwd(here)

    def do_upload(self, local_path, remote_dir):
        ftp = self._need()
        if ftp.pwd() != remote_dir:
            ftp.cwd(remote_dir)
        if os.path.isdir(local_path):
            self._upload_tree(ftp, local_path)
        else:
            self._upload_file(ftp, local_path, os.path.basename(local_path))
        if self.queued() <= 1:
            self.do_list()

    def do_refresh_remote(self):
        if self.ftp is not None:
            self.do_list()


# --------------------------------------------------------------------------
# Theme
# --------------------------------------------------------------------------

PALETTES = {
    "light": {
        "bg": "#f3f3f3", "surface": "#ffffff", "surface2": "#fafafa",
        "text": "#1b1b1b", "subtle": "#616161", "border": "#e5e5e5",
        "accent": "#005fb8", "accent_hover": "#196ebf", "accent_text": "#ffffff",
        "select": "#cfe4fa", "hover": "#eaeaea", "danger": "#c42b1c",
        "ok": "#0f7b0f", "warn": "#9d5d00", "folder": "#e8b23a", "file": "#8a8a8a",
    },
    "dark": {
        "bg": "#202020", "surface": "#2b2b2b", "surface2": "#272727",
        "text": "#f2f2f2", "subtle": "#a8a8a8", "border": "#3d3d3d",
        "accent": "#4cc2ff", "accent_hover": "#47b1e8", "accent_text": "#000000",
        "select": "#0e4a73", "hover": "#353535", "danger": "#ff99a4",
        "ok": "#6ccb5f", "warn": "#fce100", "folder": "#f0c24a", "file": "#9a9a9a",
    },
}


def system_prefers_dark() -> bool:
    if IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        except Exception:
            return False
    if sys.platform == "darwin":
        try:
            import subprocess
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                 capture_output=True, text=True, timeout=2)
            return "dark" in out.stdout.lower()
        except Exception:
            return False
    return False


def pick_font_family(root) -> str:
    from tkinter import font as tkfont
    families = set(tkfont.families(root))
    for f in ("Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Helvetica Neue",
              "Cantarell", "Noto Sans", "DejaVu Sans"):
        if f in families:
            return f
    return "TkDefaultFont"


def make_icon(root, kind: str, color: str):
    """Draw a tiny 16x16 folder / file / up icon without any image files."""
    img = tk.PhotoImage(master=root, width=16, height=16)
    if kind == "folder":
        img.put(color, to=(1, 3, 7, 5))
        img.put(color, to=(1, 5, 15, 14))
    elif kind == "file":
        img.put(color, to=(3, 1, 13, 2))
        img.put(color, to=(3, 14, 13, 15))
        img.put(color, to=(3, 1, 4, 15))
        img.put(color, to=(12, 1, 13, 15))
        img.put(color, to=(5, 5, 11, 6))
        img.put(color, to=(5, 8, 11, 9))
        img.put(color, to=(5, 11, 9, 12))
    elif kind == "drive":
        img.put(color, to=(1, 5, 15, 12))
        img.put("#ffffff", to=(11, 8, 13, 10))
    return img


# --------------------------------------------------------------------------
# File pane
# --------------------------------------------------------------------------

class FilePane(ttk.Frame):
    def __init__(self, app, master, side: str):
        super().__init__(master, style="Card.TFrame", padding=(12, 10, 12, 8))
        self.app = app
        self.side = side  # "local" or "remote"
        self.path = ""
        self.entries: dict[str, Entry] = {}
        self.history_back: list[str] = []

        head = ttk.Frame(self, style="Card.TFrame")
        head.pack(fill="x")
        self.title = ttk.Label(head, text="This PC" if side == "local" else "Server",
                               style="PaneTitle.TLabel")
        self.title.pack(side="left")
        self.status = ttk.Label(head, text="", style="Subtle.TLabel")
        self.status.pack(side="right")

        bar = ttk.Frame(self, style="Card.TFrame")
        bar.pack(fill="x", pady=(8, 6))
        self.up_btn = ttk.Button(bar, text="↑", width=3, style="Icon.TButton",
                                 command=self.go_up)
        self.up_btn.pack(side="left")
        self.refresh_btn = ttk.Button(bar, text="↻", width=3, style="Icon.TButton",
                                      command=self.refresh)
        self.refresh_btn.pack(side="left", padx=(4, 0))
        self.crumbs = ttk.Frame(bar, style="Crumb.TFrame", padding=(4, 2))
        self.crumbs.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.crumbs.bind("<Configure>", lambda e: self._layout_crumbs())
        self.crumbs.bind("<Button-1>", lambda e: self.edit_path())

        tools = ttk.Frame(self, style="Card.TFrame")
        tools.pack(fill="x", pady=(0, 6))
        if side == "local":
            self.xfer_btn = ttk.Button(tools, text="Upload  →", style="Accent.TButton",
                                       command=app.upload_selected)
        else:
            self.xfer_btn = ttk.Button(tools, text="←  Download", style="Accent.TButton",
                                       command=app.download_selected)
        self.xfer_btn.pack(side="left")
        self.new_btn = ttk.Button(tools, text="New folder", command=self.new_folder)
        self.new_btn.pack(side="left", padx=(6, 0))
        self.ren_btn = ttk.Button(tools, text="Rename", command=self.rename)
        self.ren_btn.pack(side="left", padx=(6, 0))
        self.del_btn = ttk.Button(tools, text="Delete", command=self.delete)
        self.del_btn.pack(side="left", padx=(6, 0))

        body = ttk.Frame(self, style="Card.TFrame")
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, columns=("size", "modified"), selectmode="extended",
                                 style="Files.Treeview")
        self.tree.heading("#0", text="Name", anchor="w")
        self.tree.heading("size", text="Size", anchor="e")
        self.tree.heading("modified", text="Modified", anchor="w")
        self.tree.column("#0", width=260, minwidth=120, stretch=True)
        self.tree.column("size", width=80, minwidth=60, anchor="e", stretch=False)
        self.tree.column("modified", width=150, minwidth=120, stretch=False)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.empty_label = ttk.Label(self.tree, text="", style="Empty.TLabel")

        t = self.tree
        t.bind("<Double-1>", self._on_double)
        t.bind("<Return>", self._on_double)
        t.bind("<BackSpace>", lambda e: self.go_up())
        t.bind("<F5>", lambda e: self.refresh())
        t.bind("<F2>", lambda e: self.rename())
        t.bind("<Delete>", lambda e: self.delete())
        t.bind("<Control-a>", lambda e: (t.selection_set(t.get_children()), "break")[1])
        t.bind("<Control-N>", lambda e: self.new_folder())
        t.bind("<Button-3>", self._on_context)
        if sys.platform == "darwin":
            t.bind("<Button-2>", self._on_context)
        t.bind("<ButtonPress-1>", self._drag_start, add="+")
        t.bind("<B1-Motion>", self._drag_motion, add="+")
        t.bind("<ButtonRelease-1>", self._drag_end, add="+")
        t.bind("<<TreeviewSelect>>", lambda e: self.app.update_buttons())
        self._drag = None

    # -- data ------------------------------------------------------------
    def show(self, path: str, entries):
        self.path = path
        self.entries = {}
        self.tree.delete(*self.tree.get_children())
        icons = self.app.icons
        for e in entries:
            iid = e.name
            self.entries[iid] = e
            if self.side == "local" and IS_WINDOWS and path == THIS_PC:
                icon = icons["drive"]
            else:
                icon = icons["folder"] if e.is_dir else icons["file"]
            self.tree.insert("", "end", iid=iid, text="  " + e.name, image=icon,
                             values=("" if e.is_dir else human_size(e.size),
                                     human_time(e.mtime)))
        n_dirs = sum(1 for e in entries if e.is_dir)
        n_files = len(entries) - n_dirs
        bits = []
        if n_dirs:
            bits.append(f"{n_dirs} folder" + ("s" if n_dirs != 1 else ""))
        if n_files:
            bits.append(f"{n_files} file" + ("s" if n_files != 1 else ""))
        self.status.configure(text=", ".join(bits) if bits else "")
        if entries:
            self.empty_label.place_forget()
        else:
            self.set_empty("This folder is empty")
        self._build_crumbs()
        self.app.update_buttons()

    def set_empty(self, text):
        self.empty_label.configure(text=text)
        self.empty_label.place(relx=0.5, rely=0.4, anchor="center")

    def clear(self, message=""):
        self.path = ""
        self.entries = {}
        self.tree.delete(*self.tree.get_children())
        self.status.configure(text="")
        for w in self.crumbs.winfo_children():
            w.destroy()
        if message:
            self.set_empty(message)
        self.app.update_buttons()

    def selected(self):
        return [self.entries[i] for i in self.tree.selection() if i in self.entries]

    # -- path helpers ----------------------------------------------------
    def join(self, name):
        if self.side == "remote":
            return posixpath.join(self.path, name)
        if IS_WINDOWS and self.path == THIS_PC:
            return name
        return os.path.join(self.path, name)

    def parent(self):
        if self.side == "remote":
            return posixpath.dirname(self.path.rstrip("/")) or "/"
        if IS_WINDOWS:
            if self.path == THIS_PC:
                return None
            p = os.path.dirname(self.path.rstrip("\\/"))
            if len(self.path.rstrip("\\/")) <= 2:  # "C:"
                return THIS_PC
            return p if p.endswith(("\\", "/")) or len(p) > 2 else p + "\\"
        p = os.path.dirname(self.path.rstrip("/")) or "/"
        return p

    def crumb_parts(self):
        """Return [(label, path), ...] from root to current folder."""
        if self.side == "remote":
            parts = [("/", "/")]
            acc = ""
            for seg in [s for s in self.path.split("/") if s]:
                acc += "/" + seg
                parts.append((seg, acc))
            return parts
        if IS_WINDOWS:
            parts = [("This PC", THIS_PC)]
            if self.path == THIS_PC:
                return parts
            drive, rest = os.path.splitdrive(self.path)
            acc = drive + "\\"
            parts.append((drive or "\\", acc))
            for seg in [s for s in rest.replace("/", "\\").split("\\") if s]:
                acc = os.path.join(acc, seg)
                parts.append((seg, acc))
            return parts
        parts = [("/", "/")]
        acc = "/"
        for seg in [s for s in self.path.split("/") if s]:
            acc = os.path.join(acc, seg)
            parts.append((seg, acc))
        return parts

    def _build_crumbs(self):
        for w in self.crumbs.winfo_children():
            w.destroy()
        self._crumb_widgets = []
        for label, path in self.crumb_parts():
            b = ttk.Button(self.crumbs, text=label, style="Crumb.TButton",
                           command=lambda p=path: self.navigate(p))
            sep = ttk.Label(self.crumbs, text="\u203a", style="CrumbSep.TLabel")
            self._crumb_widgets.append((b, sep, path))
        self._ellipsis = ttk.Button(self.crumbs, text="\u2026", style="Crumb.TButton")
        self._ellipsis_sep = ttk.Label(self.crumbs, text="\u203a", style="CrumbSep.TLabel")
        self.after_idle(self._layout_crumbs)

    def _layout_crumbs(self):
        widgets = getattr(self, "_crumb_widgets", [])
        if not widgets or not self._ellipsis.winfo_exists():
            return
        for w in self.crumbs.winfo_children():
            w.pack_forget()
        avail = max(self.crumbs.winfo_width() - 12, 60)
        widths = [b.winfo_reqwidth() + sep.winfo_reqwidth() for b, sep, _ in widgets]
        if sum(widths) > avail:
            avail -= self._ellipsis.winfo_reqwidth() + self._ellipsis_sep.winfo_reqwidth()
        # Keep the deepest folders visible; fold the rest into "...".
        shown, used = [], 0
        for i in range(len(widgets) - 1, -1, -1):
            if shown and used + widths[i] > avail:
                break
            used += widths[i]
            shown.insert(0, i)
        if shown[0] > 0:
            target = widgets[shown[0] - 1][2]
            self._ellipsis.configure(command=lambda p=target: self.navigate(p))
            self._ellipsis.pack(side="left")
            self._ellipsis_sep.pack(side="left")
        for n, i in enumerate(shown):
            b, sep, _ = widgets[i]
            if n > 0:
                sep.pack(side="left")
            b.pack(side="left")

    def edit_path(self):
        if self.side == "remote" and not self.app.connected:
            return
        value = simpledialog.askstring("Go to folder", "Folder path:",
                                       initialvalue=self.path, parent=self.app.root)
        if value:
            self.navigate(value.strip())

    # -- actions ---------------------------------------------------------
    def navigate(self, path):
        if self.side == "local":
            self.app.load_local(path)
        elif self.app.connected:
            self.app.worker.submit("list", path=path)

    def refresh(self):
        if self.side == "local":
            self.app.load_local(self.path)
        elif self.app.connected:
            self.app.worker.submit("list", path=None)

    def go_up(self):
        if self.side == "remote" and not self.app.connected:
            return
        p = self.parent()
        if p is not None and p != self.path:
            self.navigate(p)

    def _on_double(self, event=None):
        if event is not None and str(event.type) == "ButtonPress" and \
                self.tree.identify_region(event.x, event.y) == "heading":
            return
        sel = self.selected()
        if not sel:
            return
        if len(sel) == 1 and sel[0].is_dir:
            self.navigate(self.join(sel[0].name))
        elif self.side == "local":
            self.app.upload_selected()
        else:
            self.app.download_selected()
        return "break"

    def new_folder(self):
        if self.side == "remote" and not self.app.connected:
            return
        if self.side == "local" and IS_WINDOWS and self.path == THIS_PC:
            return
        name = simpledialog.askstring("New folder", "Folder name:", parent=self.app.root)
        if not name:
            return
        name = name.strip()
        if self.side == "local":
            try:
                os.makedirs(os.path.join(self.path, name), exist_ok=False)
                self.app.log("ok", f"Created folder {name}")
            except OSError as e:
                messagebox.showerror(APP_NAME, f"Couldn't create folder:\n{e}",
                                     parent=self.app.root)
            self.refresh()
        else:
            self.app.worker.submit("mkdir", name=name)

    def rename(self):
        sel = self.selected()
        if len(sel) != 1:
            return
        if self.side == "local" and IS_WINDOWS and self.path == THIS_PC:
            return
        old = sel[0].name
        new = simpledialog.askstring("Rename", "New name:", initialvalue=old,
                                     parent=self.app.root)
        if not new or new.strip() == old:
            return
        new = new.strip()
        if self.side == "local":
            try:
                os.rename(os.path.join(self.path, old), os.path.join(self.path, new))
                self.app.log("ok", f"Renamed {old} to {new}")
            except OSError as e:
                messagebox.showerror(APP_NAME, f"Couldn't rename:\n{e}", parent=self.app.root)
            self.refresh()
        else:
            self.app.worker.submit("rename", old=old, new=new)

    def delete(self):
        sel = self.selected()
        if not sel:
            return
        if self.side == "local" and IS_WINDOWS and self.path == THIS_PC:
            return
        what = f"“{sel[0].name}”" if len(sel) == 1 else f"these {len(sel)} items"
        where = "from the server" if self.side == "remote" else "from this PC"
        extra = ("\n\nFolders are deleted with everything inside them."
                 if any(e.is_dir for e in sel) else "")
        if not messagebox.askyesno("Delete", f"Permanently delete {what} {where}?{extra}",
                                   icon="warning", parent=self.app.root):
            return
        if self.side == "local":
            for e in sel:
                p = os.path.join(self.path, e.name)
                try:
                    if e.is_dir:
                        shutil.rmtree(p)
                    else:
                        os.remove(p)
                    self.app.log("ok", f"Deleted {e.name}")
                except OSError as ex:
                    self.app.log("error", f"Couldn't delete {e.name}: {ex}")
            self.refresh()
        else:
            self.app.worker.submit("delete", items=[(e.name, e.is_dir) for e in sel])

    # -- context menu ----------------------------------------------------
    def _on_context(self, event):
        row = self.tree.identify_row(event.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        self.tree.focus_set()
        m = tk.Menu(self, tearoff=0)
        self.app.style_menu(m)
        sel = self.selected()
        connected = self.app.connected
        if sel:
            if len(sel) == 1 and sel[0].is_dir:
                m.add_command(label="Open", command=lambda: self.navigate(self.join(sel[0].name)))
            if self.side == "local":
                m.add_command(label="Upload", command=self.app.upload_selected,
                              state="normal" if connected else "disabled")
            else:
                m.add_command(label="Download", command=self.app.download_selected)
            m.add_separator()
            m.add_command(label="Rename", accelerator="F2", command=self.rename,
                          state="normal" if len(sel) == 1 else "disabled")
            m.add_command(label="Delete", accelerator="Del", command=self.delete)
            m.add_separator()
        m.add_command(label="New folder", command=self.new_folder)
        m.add_command(label="Refresh", accelerator="F5", command=self.refresh)
        if self.side == "local" and self.path != THIS_PC:
            m.add_command(label="Open in file manager", command=self.open_in_os)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def open_in_os(self):
        try:
            if IS_WINDOWS:
                os.startfile(self.path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", self.path])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", self.path])
        except Exception as e:
            self.app.log("error", f"Couldn't open folder: {e}")

    # -- drag and drop between panes ------------------------------------
    def _drag_start(self, event):
        row = self.tree.identify_row(event.y)
        self._drag = {"x": event.x_root, "y": event.y_root, "row": row, "active": False} \
            if row else None

    def _drag_motion(self, event):
        d = self._drag
        if not d:
            return
        if not d["active"]:
            if abs(event.x_root - d["x"]) + abs(event.y_root - d["y"]) < 8:
                return
            if d["row"] not in self.tree.selection():
                self.tree.selection_set(d["row"])
            d["active"] = True
            self.app.drag_begin(self)
        self.app.drag_move(self, event.x_root, event.y_root)

    def _drag_end(self, event):
        d = self._drag
        self._drag = None
        if d and d["active"]:
            self.app.drag_drop(self, event.x_root, event.y_root)


# --------------------------------------------------------------------------
# Saved servers dialog
# --------------------------------------------------------------------------

class ServerDialog(tk.Toplevel):
    def __init__(self, app, select: str = ""):
        super().__init__(app.root)
        self.app = app
        self.title("Saved servers")
        self.transient(app.root)
        self.configure(bg=app.pal["bg"])
        self.resizable(True, True)
        self.minsize(620, 380)
        self.current: str | None = None

        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        left = ttk.Frame(outer)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Servers", style="Section.TLabel").pack(anchor="w")
        self.listbox = tk.Listbox(left, width=24, activestyle="none", exportselection=False,
                                  borderwidth=0, highlightthickness=1)
        app.style_listbox(self.listbox)
        self.listbox.pack(fill="y", expand=True, pady=(6, 6))
        self.listbox.bind("<<ListboxSelect>>", self._on_pick)
        self.listbox.bind("<Double-1>", lambda e: self._connect())
        lb = ttk.Frame(left)
        lb.pack(fill="x")
        ttk.Button(lb, text="New", command=self._new).pack(side="left")
        self.del_btn = ttk.Button(lb, text="Delete", command=self._delete)
        self.del_btn.pack(side="left", padx=(6, 0))

        right = ttk.Frame(outer)
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))
        ttk.Label(right, text="Connection details", style="Section.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.vars = {
            "name": tk.StringVar(), "host": tk.StringVar(), "port": tk.StringVar(value="21"),
            "user": tk.StringVar(), "password": tk.StringVar(),
            "save_password": tk.BooleanVar(), "tls": tk.BooleanVar(),
            "passive": tk.BooleanVar(value=True), "remote_dir": tk.StringVar(),
        }
        rows = [("Name", "name"), ("Host", "host"), ("Port", "port"), ("Username", "user"),
                ("Password", "password"), ("Start folder", "remote_dir")]
        self.entries = {}
        for r, (label, key) in enumerate(rows, start=1):
            ttk.Label(right, text=label).grid(row=r, column=0, sticky="w", pady=4, padx=(0, 12))
            e = ttk.Entry(right, textvariable=self.vars[key],
                          show="•" if key == "password" else "")
            e.grid(row=r, column=1, sticky="ew", pady=4)
            self.entries[key] = e
        right.columnconfigure(1, weight=1)
        r = len(rows) + 1
        self.save_pw_chk = ttk.Checkbutton(
            right, text="Remember password" + ("" if password_store_available()
                                                else " (needs the keyring package)"),
            variable=self.vars["save_password"])
        self.save_pw_chk.grid(row=r, column=1, sticky="w", pady=(6, 0))
        if not password_store_available():
            self.save_pw_chk.state(["disabled"])
        ttk.Checkbutton(right, text="Use FTPS (encrypted, explicit TLS)",
                        variable=self.vars["tls"]).grid(row=r + 1, column=1, sticky="w")
        ttk.Checkbutton(right, text="Passive mode (recommended)",
                        variable=self.vars["passive"]).grid(row=r + 2, column=1, sticky="w")
        self.msg = ttk.Label(right, text="", style="Subtle.TLabel")
        self.msg.grid(row=r + 3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        btns = ttk.Frame(right)
        btns.grid(row=r + 4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Connect", style="Accent.TButton",
                   command=self._connect).pack(side="right", padx=(0, 6))
        ttk.Button(btns, text="Save", command=self._save).pack(side="right", padx=(0, 6))

        self.bind("<Escape>", lambda e: self.destroy())
        self._reload(select)
        app.apply_titlebar(self)
        self.geometry(f"+{app.root.winfo_rootx() + 60}+{app.root.winfo_rooty() + 60}")
        self.grab_set()
        self.entries["name"].focus_set()

    def _reload(self, select=""):
        self.listbox.delete(0, "end")
        names = [s["name"] for s in self.app.settings.servers]
        for n in names:
            self.listbox.insert("end", n)
        if select in names:
            i = names.index(select)
            self.listbox.selection_set(i)
            self._load(select)
        elif names:
            self.listbox.selection_set(0)
            self._load(names[0])
        else:
            self._new()

    def _on_pick(self, event=None):
        sel = self.listbox.curselection()
        if sel:
            self._load(self.listbox.get(sel[0]))

    def _load(self, name):
        s = self.app.settings.get_server(name)
        if not s:
            return
        self.current = name
        for k in ("name", "host", "user", "remote_dir"):
            self.vars[k].set(s.get(k, ""))
        self.vars["port"].set(str(s.get("port", 21)))
        for k in ("tls", "passive", "save_password"):
            self.vars[k].set(bool(s.get(k)))
        self.vars["password"].set(get_saved_password(name) if s.get("save_password") else "")
        self.msg.configure(text="")
        self.del_btn.state(["!disabled"])

    def _new(self):
        self.current = None
        self.listbox.selection_clear(0, "end")
        for k in ("name", "host", "user", "password", "remote_dir"):
            self.vars[k].set("")
        self.vars["port"].set("21")
        self.vars["tls"].set(False)
        self.vars["passive"].set(True)
        self.vars["save_password"].set(False)
        self.msg.configure(text="Fill in the details and press Save.")
        self.del_btn.state(["disabled"])
        self.entries["name"].focus_set()

    def _collect(self):
        name = self.vars["name"].get().strip()
        host = self.vars["host"].get().strip()
        if not name:
            self.msg.configure(text="Give this server a name.")
            return None
        if not host:
            self.msg.configure(text="Enter the server's host name or address.")
            return None
        try:
            port = int(self.vars["port"].get().strip() or 21)
            if not 0 < port < 65536:
                raise ValueError
        except ValueError:
            self.msg.configure(text="Port must be a number between 1 and 65535.")
            return None
        if name != self.current and self.app.settings.get_server(name):
            self.msg.configure(text=f"A server called “{name}” already exists.")
            return None
        return {"name": name, "host": host, "port": port,
                "user": self.vars["user"].get().strip(),
                "tls": self.vars["tls"].get(), "passive": self.vars["passive"].get(),
                "remote_dir": self.vars["remote_dir"].get().strip(),
                "save_password": self.vars["save_password"].get() and password_store_available()}

    def _save(self):
        s = self._collect()
        if not s:
            return False
        old = self.current
        if old and old != s["name"]:
            delete_saved_password(old)
        if s["save_password"]:
            if not set_saved_password(s["name"], self.vars["password"].get()):
                s["save_password"] = False
                self.msg.configure(text="Saved, but the password couldn't be stored.")
        else:
            delete_saved_password(s["name"])
        self.app.settings.put_server(s, old_name=old)
        self.current = s["name"]
        self._reload(s["name"])
        if not self.msg.cget("text").startswith("Saved, but"):
            self.msg.configure(text="Saved.")
        self.app.refresh_server_list()
        return True

    def _delete(self):
        if not self.current:
            return
        if not messagebox.askyesno("Delete server",
                                   f"Remove “{self.current}” from saved servers?",
                                   parent=self):
            return
        delete_saved_password(self.current)
        self.app.settings.delete_server(self.current)
        self.current = None
        self._reload()
        self.app.refresh_server_list()

    def _connect(self):
        s = self._collect()
        if not s:
            return
        s["password"] = self.vars["password"].get()
        self.destroy()
        self.app.fill_fields(s)
        self.app.server_var.set(s["name"] if self.app.settings.get_server(s["name"])
                                else self.app.QUICK)
        self.app.connect()


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

class App:
    QUICK = "Quick connect"

    def __init__(self, root: tk.Tk, settings: Settings | None = None):
        self.root = root
        self.settings = settings or Settings()
        self.connected = False
        self.connecting = False
        self.ui_queue: queue.Queue = queue.Queue()
        self.worker = FTPWorker(self._post)
        self.worker.start()
        self.drag_source = None
        self.drop_hint = None

        root.title(APP_NAME)
        root.minsize(860, 520)
        self.family = pick_font_family(root)
        self.style = ttk.Style(root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        self.theme_mode = self.settings.data.get("theme", "system")
        self.pal = PALETTES["dark" if self._wants_dark() else "light"]
        self._build()
        self.apply_theme()
        self._size_window()

        start = self.settings.data.get("local_dir") or os.path.expanduser("~")
        if not os.path.isdir(start):
            start = os.path.expanduser("~")
        self.load_local(start)
        self.remote.clear("Not connected. Pick a server above and press Connect.")
        last = self.settings.data.get("last_server")
        if last and self.settings.get_server(last):
            self.server_var.set(last)
            self._on_server_pick()
        self.update_buttons()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind_all("<F5>", lambda e: self._focused_pane().refresh())
        root.bind("<Control-q>", lambda e: self.on_close())
        self._pump_id = self.root.after(50, self._pump)
        if self.theme_mode == "system":
            self.root.after(5000, self._watch_system_theme)

    # -- layout ----------------------------------------------------------
    def _build(self):
        root = self.root
        self.main = ttk.Frame(root, padding=(14, 12, 14, 10))
        self.main.pack(fill="both", expand=True)

        # Connection bar
        conn = ttk.Frame(self.main, style="Card.TFrame", padding=(12, 10))
        conn.pack(fill="x")
        self.conn_frame = conn

        self.server_var = tk.StringVar(value=self.QUICK)
        self.server_box = ttk.Combobox(conn, textvariable=self.server_var, state="readonly",
                                       width=18)
        self.server_box.grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.server_box.bind("<<ComboboxSelected>>", lambda e: self._on_server_pick())

        self.host_var = tk.StringVar()
        self.port_var = tk.StringVar(value="21")
        self.user_var = tk.StringVar()
        self.pass_var = tk.StringVar()
        self.tls_var = tk.BooleanVar(value=False)
        self.passive = True
        self.remote_dir = ""

        def field(col, label, var, width, show=""):
            ttk.Label(conn, text=label, style="CardSubtle.TLabel").grid(
                row=0, column=col, sticky="w", padx=(0, 4))
            e = ttk.Entry(conn, textvariable=var, width=width, show=show)
            e.grid(row=0, column=col + 1, sticky="ew", padx=(0, 10))
            e.bind("<Return>", lambda ev: self.connect())
            return e

        self.host_entry = field(1, "Host", self.host_var, 22)
        self.port_entry = field(3, "Port", self.port_var, 6)
        self.user_entry = field(5, "User", self.user_var, 14)
        self.pass_entry = field(7, "Password", self.pass_var, 14, show="•")
        conn.columnconfigure(2, weight=3)
        conn.columnconfigure(6, weight=1)
        conn.columnconfigure(8, weight=1)

        self.tls_chk = ttk.Checkbutton(conn, text="FTPS", variable=self.tls_var,
                                       style="Card.TCheckbutton")
        self.tls_chk.grid(row=0, column=9, padx=(0, 10))
        self.connect_btn = ttk.Button(conn, text="Connect", style="Accent.TButton",
                                      command=self.toggle_connect, width=11)
        self.connect_btn.grid(row=0, column=10)
        self.save_btn = ttk.Button(conn, text="Save…", command=self.save_current)
        self.save_btn.grid(row=0, column=11, padx=(6, 0))
        self.manage_btn = ttk.Button(conn, text="Servers…",
                                     command=lambda: ServerDialog(self, self.server_var.get()))
        self.manage_btn.grid(row=0, column=12, padx=(6, 0))
        self.theme_btn = ttk.Button(conn, text="", width=3, style="Icon.TButton",
                                    command=self.cycle_theme)
        self.theme_btn.grid(row=0, column=13, padx=(6, 0))

        # Panes
        self.panes = ttk.PanedWindow(self.main, orient="horizontal")
        self.panes.pack(fill="both", expand=True, pady=(10, 0))
        self.local = FilePane(self, self.panes, "local")
        self.remote = FilePane(self, self.panes, "remote")
        self.panes.add(self.local, weight=1)
        self.panes.add(self.remote, weight=1)

        # Transfer bar
        xfer = ttk.Frame(self.main, style="Card.TFrame", padding=(12, 8))
        xfer.pack(fill="x", pady=(10, 0))
        self.xfer_label = ttk.Label(xfer, text="No transfers", style="Card.TLabel")
        self.xfer_label.pack(side="left")
        self.log_btn = ttk.Button(xfer, text="", style="Link.TButton", command=self.toggle_log)
        self.log_btn.pack(side="right")
        self.cancel_btn = ttk.Button(xfer, text="Cancel", command=self.cancel_transfers)
        self.queue_label = ttk.Label(xfer, text="", style="CardSubtle.TLabel")
        self.queue_label.pack(side="right", padx=(0, 12))
        self.speed_label = ttk.Label(xfer, text="", style="CardSubtle.TLabel")
        self.speed_label.pack(side="right", padx=(0, 12))
        self.progress = ttk.Progressbar(xfer, mode="determinate", length=200,
                                        style="Accent.Horizontal.TProgressbar")
        self.progress.pack(side="right", padx=(12, 12))

        # Log
        self.log_frame = ttk.Frame(self.main, style="Card.TFrame", padding=(12, 8))
        self.log_text = tk.Text(self.log_frame, height=7, wrap="word", borderwidth=0,
                                highlightthickness=0, state="disabled")
        lsb = ttk.Scrollbar(self.log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")
        self.log_open = bool(self.settings.data.get("log_open"))
        self._apply_log_visibility()

        self.refresh_server_list()

    def _size_window(self):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = min(max(1100, int(sw * 0.6)), sw - 80)
        h = min(max(680, int(sh * 0.65)), sh - 120)
        x, y = max((sw - w) // 2, 0), max((sh - h) // 3, 0)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # -- theme -----------------------------------------------------------
    def _wants_dark(self):
        if self.theme_mode == "dark":
            return True
        if self.theme_mode == "light":
            return False
        return system_prefers_dark()

    def cycle_theme(self):
        order = ["system", "light", "dark"]
        self.theme_mode = order[(order.index(self.theme_mode) + 1) % 3]
        self.settings.data["theme"] = self.theme_mode
        self._save_settings()
        self.pal = PALETTES["dark" if self._wants_dark() else "light"]
        self.apply_theme()
        self.log("info", f"Theme: {self.theme_mode}")
        if self.theme_mode == "system":
            self.root.after(5000, self._watch_system_theme)

    def _watch_system_theme(self):
        if self.theme_mode != "system":
            return
        want = PALETTES["dark" if system_prefers_dark() else "light"]
        if want is not self.pal:
            self.pal = want
            self.apply_theme()
        self.root.after(5000, self._watch_system_theme)

    def apply_theme(self):
        p = self.pal
        f = self.family
        s = self.style
        base = (f, 10)
        s.configure(".", background=p["bg"], foreground=p["text"], font=base,
                    bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"],
                    troughcolor=p["bg"], focuscolor=p["accent"], fieldbackground=p["surface"],
                    selectbackground=p["select"], selectforeground=p["text"],
                    insertcolor=p["text"])
        s.configure("TFrame", background=p["bg"])
        s.configure("Card.TFrame", background=p["surface"], relief="solid", borderwidth=0)
        s.configure("Crumb.TFrame", background=p["surface2"], relief="solid", borderwidth=1,
                    bordercolor=p["border"])
        s.configure("TLabel", background=p["bg"], foreground=p["text"])
        s.configure("Card.TLabel", background=p["surface"], foreground=p["text"])
        s.configure("CardSubtle.TLabel", background=p["surface"], foreground=p["subtle"])
        s.configure("Subtle.TLabel", background=p["surface"], foreground=p["subtle"],
                    font=(f, 9))
        s.configure("Section.TLabel", background=p["bg"], foreground=p["text"],
                    font=(f, 11, "bold"))
        s.configure("PaneTitle.TLabel", background=p["surface"], foreground=p["text"],
                    font=(f, 12, "bold"))
        s.configure("Empty.TLabel", background=p["surface"], foreground=p["subtle"],
                    font=(f, 10))
        s.configure("CrumbSep.TLabel", background=p["surface2"], foreground=p["subtle"])

        s.configure("TButton", background=p["surface"], foreground=p["text"],
                    bordercolor=p["border"], lightcolor=p["surface"], darkcolor=p["surface"],
                    padding=(12, 5), relief="solid", borderwidth=1, focusthickness=0)
        s.map("TButton",
              background=[("disabled", p["surface"]), ("pressed", p["hover"]),
                          ("active", p["hover"])],
              foreground=[("disabled", p["subtle"])],
              lightcolor=[("active", p["hover"])], darkcolor=[("active", p["hover"])])
        s.configure("Accent.TButton", background=p["accent"], foreground=p["accent_text"],
                    bordercolor=p["accent"], lightcolor=p["accent"], darkcolor=p["accent"],
                    font=(f, 10, "bold"))
        s.map("Accent.TButton",
              background=[("disabled", p["border"]), ("pressed", p["accent_hover"]),
                          ("active", p["accent_hover"])],
              foreground=[("disabled", p["subtle"])],
              bordercolor=[("disabled", p["border"])],
              lightcolor=[("disabled", p["border"]), ("active", p["accent_hover"])],
              darkcolor=[("disabled", p["border"]), ("active", p["accent_hover"])])
        s.configure("Icon.TButton", padding=(4, 4), font=(f, 11), width=0)
        s.configure("Crumb.TButton", background=p["surface2"], foreground=p["text"],
                    bordercolor=p["surface2"], lightcolor=p["surface2"],
                    darkcolor=p["surface2"], padding=(5, 1), borderwidth=0, width=0)
        s.map("Crumb.TButton", background=[("active", p["hover"])],
              lightcolor=[("active", p["hover"])], darkcolor=[("active", p["hover"])])
        s.configure("Link.TButton", background=p["surface"], foreground=p["accent"],
                    bordercolor=p["surface"], lightcolor=p["surface"], darkcolor=p["surface"],
                    padding=(4, 2), borderwidth=0, width=0)
        s.map("Link.TButton", background=[("active", p["hover"])],
              lightcolor=[("active", p["hover"])], darkcolor=[("active", p["hover"])])

        s.configure("TEntry", fieldbackground=p["surface2"], foreground=p["text"],
                    bordercolor=p["border"], lightcolor=p["surface2"], darkcolor=p["surface2"],
                    padding=(6, 4), insertcolor=p["text"])
        s.map("TEntry", bordercolor=[("focus", p["accent"])],
              lightcolor=[("focus", p["accent"])])
        s.configure("TCombobox", fieldbackground=p["surface2"], background=p["surface2"],
                    foreground=p["text"], arrowcolor=p["text"], bordercolor=p["border"],
                    lightcolor=p["surface2"], darkcolor=p["surface2"], padding=(6, 4))
        s.map("TCombobox", fieldbackground=[("readonly", p["surface2"])],
              foreground=[("readonly", p["text"])],
              selectbackground=[("readonly", p["surface2"])],
              selectforeground=[("readonly", p["text"])],
              bordercolor=[("focus", p["accent"])])
        self.root.option_add("*TCombobox*Listbox.background", p["surface"])
        self.root.option_add("*TCombobox*Listbox.foreground", p["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", p["select"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", p["text"])
        for name in ("TCheckbutton", "Card.TCheckbutton"):
            bg = p["surface"] if name.startswith("Card") else p["bg"]
            s.configure(name, background=bg, foreground=p["text"],
                        indicatorbackground=p["surface2"], indicatorforeground=p["accent"])
            s.map(name, background=[("active", bg)],
                  indicatorbackground=[("selected", p["accent"]), ("active", p["hover"])],
                  indicatorforeground=[("selected", p["accent_text"])])

        s.configure("Files.Treeview", background=p["surface"], fieldbackground=p["surface"],
                    foreground=p["text"], bordercolor=p["surface"], lightcolor=p["surface"],
                    darkcolor=p["surface"], rowheight=26, borderwidth=0, font=base)
        s.map("Files.Treeview", background=[("selected", p["select"])],
              foreground=[("selected", p["text"])])
        s.configure("Files.Treeview.Heading", background=p["surface"], foreground=p["subtle"],
                    bordercolor=p["border"], lightcolor=p["surface"], darkcolor=p["surface"],
                    relief="flat", font=(f, 9), padding=(4, 4))
        s.map("Files.Treeview.Heading", background=[("active", p["hover"])])
        s.layout("Files.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        s.configure("TScrollbar", background=p["surface"], troughcolor=p["surface"],
                    bordercolor=p["surface"], lightcolor=p["surface"], darkcolor=p["surface"],
                    arrowcolor=p["subtle"], gripcount=0)
        s.map("TScrollbar", background=[("active", p["hover"])])
        s.configure("Accent.Horizontal.TProgressbar", background=p["accent"],
                    troughcolor=p["border"], bordercolor=p["border"],
                    lightcolor=p["accent"], darkcolor=p["accent"], thickness=6)
        s.configure("TPanedwindow", background=p["bg"])
        s.configure("Sash", sashthickness=10, background=p["bg"], gripcount=0,
                    lightcolor=p["bg"], bordercolor=p["bg"])

        self.root.configure(bg=p["bg"])
        self.log_text.configure(bg=p["surface"], fg=p["text"], insertbackground=p["text"],
                                selectbackground=p["select"], font=(f, 9))
        for tag in ("ok", "warn", "error", "server", "info"):
            color = {"ok": p["ok"], "warn": p["warn"], "error": p["danger"],
                     "server": p["subtle"], "info": p["text"]}[tag]
            self.log_text.tag_configure(tag, foreground=color)
        self.log_text.tag_configure("time", foreground=p["subtle"])

        self.icons = {"folder": make_icon(self.root, "folder", p["folder"]),
                      "file": make_icon(self.root, "file", p["file"]),
                      "drive": make_icon(self.root, "drive", p["subtle"])}
        for pane in (self.local, self.remote):
            for iid in pane.tree.get_children():
                e = pane.entries.get(iid)
                if e is None:
                    continue
                if pane.side == "local" and IS_WINDOWS and pane.path == THIS_PC:
                    pane.tree.item(iid, image=self.icons["drive"])
                else:
                    pane.tree.item(iid, image=self.icons["folder" if e.is_dir else "file"])
        glyph = {"system": "◐", "light": "☀", "dark": "☾"}[self.theme_mode]
        self.theme_btn.configure(text=glyph)
        self.apply_titlebar(self.root)

    def apply_titlebar(self, win):
        """Match the Windows title bar to the theme (Windows 10 20H1+ / 11)."""
        if not IS_WINDOWS:
            return
        try:
            import ctypes
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            value = ctypes.c_int(1 if self.pal is PALETTES["dark"] else 0)
            for attr in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                    break
        except Exception:
            pass

    def style_menu(self, m: tk.Menu):
        p = self.pal
        m.configure(bg=p["surface"], fg=p["text"], activebackground=p["select"],
                    activeforeground=p["text"], disabledforeground=p["subtle"],
                    borderwidth=1, relief="solid", font=(self.family, 10))

    def style_listbox(self, lb: tk.Listbox):
        p = self.pal
        lb.configure(bg=p["surface"], fg=p["text"], selectbackground=p["select"],
                     selectforeground=p["text"], highlightbackground=p["border"],
                     highlightcolor=p["accent"], font=(self.family, 10))

    # -- logging ---------------------------------------------------------
    def log(self, level, message):
        t = self.log_text
        t.configure(state="normal")
        t.insert("end", time.strftime("%H:%M:%S  "), "time")
        t.insert("end", message + "\n", level)
        lines = int(t.index("end-1c").split(".")[0])
        if lines > 2000:
            t.delete("1.0", f"{lines - 2000}.0")
        t.see("end")
        t.configure(state="disabled")

    def toggle_log(self):
        self.log_open = not self.log_open
        self.settings.data["log_open"] = self.log_open
        self._save_settings()
        self._apply_log_visibility()

    def _apply_log_visibility(self):
        if self.log_open:
            self.log_frame.pack(fill="both", pady=(10, 0))
            self.log_btn.configure(text="Hide activity ▾")
        else:
            self.log_frame.pack_forget()
            self.log_btn.configure(text="Show activity ▸")

    # -- worker -> UI ----------------------------------------------------
    def _post(self, kind, payload):
        self.ui_queue.put((kind, payload))

    def _pump(self):
        try:
            for _ in range(200):
                kind, payload = self.ui_queue.get_nowait()
                getattr(self, "on_" + kind)(payload)
        except queue.Empty:
            pass
        self._pump_id = self.root.after(50, self._pump)

    def on_log(self, payload):
        self.log(*payload)

    def on_error(self, payload):
        msg = payload["message"]
        self.log("error", msg)
        if payload["job"] == "connect":
            self.connecting = False
            self.connected = False
            self.remote.clear("Couldn't connect.")
            self.update_buttons()
            messagebox.showerror(APP_NAME, f"Couldn't connect:\n\n{msg}", parent=self.root)
        elif payload["job"] in ("mkdir", "rename", "delete", "upload", "download", "list"):
            messagebox.showerror(APP_NAME, msg, parent=self.root)
            if payload["job"] in ("upload", "download"):
                self.xfer_label.configure(text="Transfer failed")

    def on_cert_error(self, payload):
        self.connecting = False
        self.update_buttons()
        p = payload["params"]
        self.log("warn", f"Certificate check failed: {payload['error']}")
        if messagebox.askyesno(
                "Certificate not trusted",
                f"The server {p['host']} sent a certificate that couldn't be verified.\n\n"
                f"{payload['error']}\n\nThe connection would still be encrypted, but you "
                "can't be sure who you're talking to. Connect anyway?",
                icon="warning", parent=self.root):
            p = dict(p)
            p["verify"] = False
            self.connecting = True
            self.update_buttons()
            self.worker.submit("connect", **p)
        else:
            self.remote.clear("Not connected.")

    def on_connected(self, payload):
        self.connecting = False
        self.connected = True
        self.remote.title.configure(text=payload["host"] + ("  \U0001f512" if payload["tls"] else ""))
        self.log("ok", f"Connected to {payload['host']}" +
                 (" (encrypted)" if payload["tls"] else ""))
        self.update_buttons()

    def on_disconnected(self, payload):
        was = self.connected
        self.connected = False
        self.connecting = False
        self.remote.title.configure(text="Server")
        self.remote.clear("Not connected." if not payload.get("unexpected")
                          else "Connection lost. Press Connect to try again.")
        if was:
            self.log("warn" if payload.get("unexpected") else "info",
                     "Connection lost." if payload.get("unexpected") else "Disconnected.")
        self.update_buttons()

    def on_listing(self, payload):
        self.remote.show(payload["path"], payload["entries"])

    def on_local_changed(self, payload):
        if os.path.normcase(os.path.abspath(payload["path"])) == \
                os.path.normcase(os.path.abspath(self.local.path or ".")):
            if self.worker.queued() <= 1:
                self.load_local(self.local.path, keep_selection=True)

    def on_progress(self, p):
        total = p["total"]
        if total:
            self.progress.configure(mode="determinate", maximum=total, value=p["done"])
            pct = int(100 * p["done"] / total) if total else 0
            self.xfer_label.configure(text=f"{p['label']}  —  {pct}%")
        else:
            self.progress.configure(mode="determinate", maximum=1, value=0)
            self.xfer_label.configure(text=f"{p['label']}  —  {human_size(p['done'])}")
        self.speed_label.configure(text=f"{human_size(p['speed'])}/s")
        if p.get("finished"):
            self.xfer_label.configure(text=p["label"].replace("Downloading", "Downloaded")
                                      .replace("Uploading", "Uploaded"))

    def on_queue(self, left):
        if left:
            self.queue_label.configure(text=f"{left} in queue")
            if not self.cancel_btn.winfo_ismapped():
                self.cancel_btn.pack(side="right", padx=(0, 12), before=self.queue_label)
        else:
            self.queue_label.configure(text="")
            self.speed_label.configure(text="")
            self.cancel_btn.pack_forget()
            self.progress.configure(value=0)
            if self.local.path is not None:
                self.load_local(self.local.path, keep_selection=True)

    # -- local pane ------------------------------------------------------
    def load_local(self, path, keep_selection=False):
        if not (IS_WINDOWS and path == THIS_PC):
            path = os.path.abspath(os.path.expanduser(path))
        try:
            entries = list_local(path)
        except OSError as e:
            self.log("error", f"Couldn't open {path}: {e}")
            if not self.local.path and path != os.path.expanduser("~"):
                self.load_local(os.path.expanduser("~"))
            else:
                messagebox.showerror(APP_NAME, f"Couldn't open folder:\n{e}", parent=self.root)
            return
        sel = self.local.tree.selection() if keep_selection else ()
        self.local.show(path, entries)
        keep = [i for i in sel if self.local.tree.exists(i)]
        if keep:
            self.local.tree.selection_set(keep)
        if path != THIS_PC:
            self.settings.data["local_dir"] = path

    def _focused_pane(self):
        w = self.root.focus_get()
        while w is not None:
            if w is self.remote:
                return self.remote
            if w is self.local:
                return self.local
            w = w.master
        return self.local

    # -- servers ---------------------------------------------------------
    def refresh_server_list(self):
        names = [s["name"] for s in self.settings.servers]
        self.server_box.configure(values=[self.QUICK] + names)
        if self.server_var.get() not in names:
            self.server_var.set(self.QUICK)

    def fill_fields(self, s):
        self.host_var.set(s.get("host", ""))
        self.port_var.set(str(s.get("port", 21)))
        self.user_var.set(s.get("user", ""))
        self.tls_var.set(bool(s.get("tls")))
        self.passive = bool(s.get("passive", True))
        self.remote_dir = s.get("remote_dir", "")
        if "password" in s:
            self.pass_var.set(s["password"])
        elif s.get("save_password"):
            self.pass_var.set(get_saved_password(s["name"]))
        else:
            self.pass_var.set("")

    def _on_server_pick(self):
        name = self.server_var.get()
        if name == self.QUICK:
            for v in (self.host_var, self.user_var, self.pass_var):
                v.set("")
            self.port_var.set("21")
            self.tls_var.set(False)
            self.passive = True
            self.remote_dir = ""
            self.host_entry.focus_set()
            return
        s = self.settings.get_server(name)
        if s:
            self.fill_fields(s)
            (self.pass_entry if not self.pass_var.get() else self.connect_btn).focus_set()

    def save_current(self):
        host = self.host_var.get().strip()
        if not host:
            messagebox.showinfo(APP_NAME, "Enter a host first.", parent=self.root)
            return
        current = self.server_var.get()
        default = current if current != self.QUICK else host
        name = simpledialog.askstring("Save server", "Save these details as:",
                                      initialvalue=default, parent=self.root)
        if not name:
            return
        name = name.strip()
        existing = self.settings.get_server(name)
        if existing and name != current and not messagebox.askyesno(
                APP_NAME, f"Replace the saved server “{name}”?", parent=self.root):
            return
        save_pw = False
        if self.pass_var.get() and password_store_available():
            save_pw = messagebox.askyesno(
                APP_NAME, "Remember the password too?\n\nIt will be kept in "
                + ("Windows Credential Manager" if IS_WINDOWS else "your system keychain")
                + ", not in the settings file.", parent=self.root)
        try:
            port = int(self.port_var.get() or 21)
        except ValueError:
            port = 21
        s = {"name": name, "host": host, "port": port, "user": self.user_var.get().strip(),
             "tls": self.tls_var.get(), "passive": self.passive,
             "remote_dir": self.remote_dir or (self.remote.path if self.connected else ""),
             "save_password": False}
        if save_pw:
            s["save_password"] = set_saved_password(name, self.pass_var.get())
            if not s["save_password"]:
                self.log("warn", "Couldn't store the password.")
        else:
            delete_saved_password(name)
        self.settings.put_server(s)
        self.refresh_server_list()
        self.server_var.set(name)
        self.log("ok", f"Saved server “{name}”")

    # -- connection ------------------------------------------------------
    def toggle_connect(self):
        if self.connected or self.connecting:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        host = self.host_var.get().strip()
        if not host:
            messagebox.showinfo(APP_NAME, "Enter the server's host name or address.",
                                parent=self.root)
            self.host_entry.focus_set()
            return
        if "://" in host:
            scheme, host = host.split("://", 1)
            if scheme.lower() == "ftps":
                self.tls_var.set(True)
            host = host.split("/", 1)[0]
            self.host_var.set(host)
        try:
            port = int(self.port_var.get().strip() or 21)
        except ValueError:
            messagebox.showinfo(APP_NAME, "Port must be a number.", parent=self.root)
            return
        if self.connected:
            self.worker.submit("disconnect")
        self.connecting = True
        self.remote.clear("Connecting…")
        self.update_buttons()
        name = self.server_var.get()
        if name != self.QUICK:
            self.settings.data["last_server"] = name
            self._save_settings()
        self.worker.submit("connect", host=host, port=port, user=self.user_var.get().strip(),
                           password=self.pass_var.get(), tls=self.tls_var.get(),
                           passive=self.passive, remote_dir=self.remote_dir)

    def disconnect(self):
        self.worker.cancel_transfers()
        self.worker.submit("disconnect")

    def update_buttons(self):
        busy = self.connecting
        self.connect_btn.configure(text="Connecting…" if busy else
                                   ("Disconnect" if self.connected else "Connect"))
        for w in (self.host_entry, self.port_entry, self.user_entry, self.pass_entry,
                  self.tls_chk, self.server_box):
            if self.connected or busy:
                w.state(["disabled"])
            else:
                w.state(["!disabled"])
                if w is self.server_box:
                    w.state(["readonly"])
        lsel = self.local.selected() if hasattr(self, "local") else []
        rsel = self.remote.selected() if hasattr(self, "remote") else []
        on_drives = IS_WINDOWS and self.local.path == THIS_PC
        self.local.xfer_btn.state(["!disabled"] if (self.connected and lsel and not on_drives)
                                  else ["disabled"])
        self.remote.xfer_btn.state(["!disabled"] if (self.connected and rsel and not on_drives)
                                   else ["disabled"])
        for pane, sel, ok in ((self.local, lsel, not on_drives),
                              (self.remote, rsel, self.connected)):
            pane.new_btn.state(["!disabled"] if ok else ["disabled"])
            pane.ren_btn.state(["!disabled"] if ok and len(sel) == 1 else ["disabled"])
            pane.del_btn.state(["!disabled"] if ok and sel else ["disabled"])
            pane.up_btn.state(["!disabled"] if (ok or pane is self.local) else ["disabled"])
            pane.refresh_btn.state(["!disabled"] if (ok or pane is self.local)
                                   else ["disabled"])

    # -- transfers -------------------------------------------------------
    def _confirm_overwrite(self, names, existing):
        clash = [n for n in names if n in existing]
        if not clash:
            return True
        what = f"“{clash[0]}” already exists" if len(clash) == 1 else \
            f"{len(clash)} items already exist"
        return messagebox.askyesno(APP_NAME, f"{what} in the destination.\n\nReplace?",
                                   parent=self.root)

    def upload_selected(self, items=None, remote_dir=None):
        if not self.connected:
            return
        items = items if items is not None else self.local.selected()
        if not items or (IS_WINDOWS and self.local.path == THIS_PC):
            return
        remote_dir = remote_dir or self.remote.path
        existing = self.remote.entries if remote_dir == self.remote.path else {}
        if not self._confirm_overwrite([e.name for e in items], existing):
            return
        for e in items:
            self.worker.submit("upload", transfer=True,
                               local_path=os.path.join(self.local.path, e.name),
                               remote_dir=remote_dir)
        self.on_queue(self.worker.queued())

    def download_selected(self, items=None, local_dir=None):
        if not self.connected:
            return
        items = items if items is not None else self.remote.selected()
        local_dir = local_dir or self.local.path
        if not items or (IS_WINDOWS and local_dir == THIS_PC):
            return
        existing = self.local.entries if local_dir == self.local.path else {}
        if not self._confirm_overwrite([e.name for e in items], existing):
            return
        for e in items:
            self.worker.submit("download", transfer=True, name=e.name, is_dir=e.is_dir,
                               local_dir=local_dir, remote_dir=self.remote.path)
        self.on_queue(self.worker.queued())

    def cancel_transfers(self):
        self.worker.cancel_transfers()
        self.xfer_label.configure(text="Cancelling…")

    # -- drag and drop ---------------------------------------------------
    def drag_begin(self, source):
        self.drag_source = source
        n = len(source.selected())
        p = self.pal
        hint = tk.Toplevel(self.root)
        hint.overrideredirect(True)
        try:
            hint.attributes("-topmost", True)
            hint.attributes("-alpha", 0.92)
        except tk.TclError:
            pass
        verb = "Upload" if source.side == "local" else "Download"
        tk.Label(hint, text=f"{verb} {n} item" + ("s" if n != 1 else ""),
                 bg=p["accent"], fg=p["accent_text"], padx=10, pady=4,
                 font=(self.family, 9, "bold")).pack()
        self.drop_hint = hint
        source.tree.configure(cursor="hand2")

    def _pane_at(self, x, y):
        w = self.root.winfo_containing(x, y)
        while w is not None:
            if w is self.local or w is self.remote:
                return w
            w = getattr(w, "master", None)
        return None

    def drag_move(self, source, x, y):
        if self.drop_hint is not None:
            self.drop_hint.geometry(f"+{x + 14}+{y + 10}")
            target = self._pane_at(x, y)
            ok = target is not None and target is not source and self.connected
            lbl = self.drop_hint.winfo_children()[0]
            lbl.configure(bg=self.pal["accent"] if ok else self.pal["subtle"])

    def drag_drop(self, source, x, y):
        if self.drop_hint is not None:
            self.drop_hint.destroy()
            self.drop_hint = None
        source.tree.configure(cursor="")
        target = self._pane_at(x, y)
        self.drag_source = None
        if target is None or target is source or not self.connected:
            return
        items = source.selected()
        # Dropped onto a folder row in the target? Put the items inside it.
        ty = y - target.tree.winfo_rooty()
        row = target.tree.identify_row(ty) if 0 <= ty <= target.tree.winfo_height() else ""
        dest_entry = target.entries.get(row) if row else None
        if source.side == "local":
            dest = posixpath.join(target.path, dest_entry.name) \
                if dest_entry and dest_entry.is_dir else None
            self.upload_selected(items, remote_dir=dest)
        else:
            dest = None
            if dest_entry and dest_entry.is_dir:
                dest = target.join(dest_entry.name)
            self.download_selected(items, local_dir=dest)

    # -- shutdown --------------------------------------------------------
    def _save_settings(self):
        try:
            self.settings.save()
        except OSError as e:
            self.log("error", f"Couldn't save settings: {e}")

    def on_close(self):
        if self.worker.queued() and not messagebox.askyesno(
                APP_NAME, "Transfers are still running. Quit anyway?", parent=self.root):
            return
        self._save_settings()
        self.worker.cancel_transfers()
        self.worker.submit("quit")
        self.root.after_cancel(self._pump_id)
        self.root.after(300, self.root.destroy)


def enable_dpi_awareness():
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def main():
    if "--version" in sys.argv:
        if sys.stdout:  # windowed builds have no console
            print(f"{APP_NAME} {APP_VERSION}")
        return
    enable_dpi_awareness()
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "HamiltonGroup.MoreSimpleFTP")
        except Exception:
            pass
    root = tk.Tk()
    try:
        # tk scaling follows the real DPI once the process is DPI aware.
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except tk.TclError:
        pass
    ico = resource_path(os.path.join("assets", "more_simple_ftp.ico"))
    png = resource_path(os.path.join("assets", "more_simple_ftp.png"))
    try:
        if IS_WINDOWS and os.path.exists(ico):
            root.iconbitmap(default=ico)
        elif os.path.exists(png):
            root.iconphoto(True, tk.PhotoImage(file=png))
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
