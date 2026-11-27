#!/usr/bin/env python3
"""More Simple FTP - command-line client.

Usage examples:
    python ftpclient.py ftp.example.com -u alice            # interactive shell
    python ftpclient.py ftp.example.com -u alice ls /pub    # one-shot command
    python ftpclient.py ftp.example.com --tls get file.txt
    python ftpclient.py ftp.example.com -u alice --save work # connect and save as "work"
    python ftpclient.py work ls                               # use the saved connection
    python ftpclient.py --saved                               # list saved connections

Commands: ls, cd, pwd, get, put, mkdir, rm, rmdir, help, quit
"""

from __future__ import annotations

import argparse
import cmd
import ftplib
import getpass
import os
import posixpath
import shlex
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional

__version__ = "1.0.0"

ProgressCallback = Callable[[int, Optional[int]], None]


@dataclass
class RemoteEntry:
    name: str
    is_dir: bool
    size: Optional[int] = None
    modified: str = ""


class FTPClient:
    """Thin, friendly wrapper around ftplib shared by the CLI and the GUI."""

    def __init__(self) -> None:
        self.ftp: Optional[ftplib.FTP] = None
        self.host = ""

    # ------------------------------------------------------------ connection
    @property
    def connected(self) -> bool:
        return self.ftp is not None

    def connect(
        self,
        host: str,
        port: int = 21,
        user: str = "anonymous",
        password: str = "",
        tls: bool = False,
        passive: bool = True,
        timeout: float = 30.0,
    ) -> str:
        self.close()
        ftp: ftplib.FTP = ftplib.FTP_TLS(timeout=timeout) if tls else ftplib.FTP(timeout=timeout)
        ftp.encoding = "utf-8"
        welcome = ftp.connect(host, port)
        ftp.login(user or "anonymous", password)
        if tls:
            ftp.prot_p()  # type: ignore[attr-defined]
        ftp.set_pasv(passive)
        self.ftp = ftp
        self.host = host
        return welcome

    def close(self) -> None:
        if self.ftp is None:
            return
        try:
            self.ftp.quit()
        except Exception:
            try:
                self.ftp.close()
            except Exception:
                pass
        self.ftp = None

    def _require(self) -> ftplib.FTP:
        if self.ftp is None:
            raise RuntimeError("Not connected")
        return self.ftp

    # ------------------------------------------------------------ navigation
    def pwd(self) -> str:
        return self._require().pwd()

    def cd(self, path: str) -> str:
        ftp = self._require()
        ftp.cwd(path)
        return ftp.pwd()

    def listdir(self, path: str = "") -> List[RemoteEntry]:
        """List a remote directory. Uses MLSD when available, else LIST parsing."""
        ftp = self._require()
        entries: List[RemoteEntry] = []
        try:
            for name, facts in ftp.mlsd(path or "", facts=["type", "size", "modify"]):
                kind = facts.get("type", "file").lower()
                if kind in ("cdir", "pdir") or name in (".", ".."):
                    continue
                size = facts.get("size")
                entries.append(
                    RemoteEntry(
                        name=name,
                        is_dir=kind == "dir",
                        size=int(size) if size and size.isdigit() else None,
                        modified=_fmt_modify(facts.get("modify", "")),
                    )
                )
        except ftplib.error_perm:
            lines: List[str] = []
            ftp.retrlines("LIST " + path if path else "LIST", lines.append)
            entries = [e for e in (_parse_list_line(l) for l in lines) if e]
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    # ------------------------------------------------------------ transfers
    def size(self, remote: str) -> Optional[int]:
        ftp = self._require()
        try:
            ftp.voidcmd("TYPE I")
            return ftp.size(remote)
        except ftplib.all_errors:
            return None

    def get(self, remote: str, local: Optional[str] = None,
            progress: Optional[ProgressCallback] = None) -> str:
        ftp = self._require()
        local = local or posixpath.basename(remote.rstrip("/"))
        if os.path.isdir(local):
            local = os.path.join(local, posixpath.basename(remote.rstrip("/")))
        total = self.size(remote)
        done = 0
        tmp = local + ".part"
        try:
            with open(tmp, "wb") as fh:
                def write(chunk: bytes) -> None:
                    nonlocal done
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
                ftp.retrbinary("RETR " + remote, write)
            os.replace(tmp, local)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return local

    def put(self, local: str, remote: Optional[str] = None,
            progress: Optional[ProgressCallback] = None) -> str:
        ftp = self._require()
        remote = remote or os.path.basename(local)
        total = os.path.getsize(local)
        done = 0

        def cb(chunk: bytes) -> None:
            nonlocal done
            done += len(chunk)
            if progress:
                progress(done, total)

        with open(local, "rb") as fh:
            ftp.storbinary("STOR " + remote, fh, callback=cb)
        return remote

    # ------------------------------------------------------------ file ops
    def mkdir(self, path: str) -> str:
        return self._require().mkd(path)

    def rm(self, path: str) -> None:
        self._require().delete(path)

    def rmdir(self, path: str, recursive: bool = False) -> None:
        ftp = self._require()
        if recursive:
            for entry in self.listdir(path):
                child = posixpath.join(path, entry.name)
                if entry.is_dir:
                    self.rmdir(child, recursive=True)
                else:
                    ftp.delete(child)
        ftp.rmd(path)

    def rename(self, old: str, new: str) -> None:
        self._require().rename(old, new)


# ---------------------------------------------------------------- helpers
def _fmt_modify(value: str) -> str:
    if len(value) >= 12 and value[:12].isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]} {value[8:10]}:{value[10:12]}"
    return value


def _parse_list_line(line: str) -> Optional[RemoteEntry]:
    """Parse a Unix-style or DOS-style LIST line."""
    parts = line.split(None, 8)
    if len(parts) == 9 and parts[0][:1] in "-dl":
        name = parts[8]
        if parts[0].startswith("l") and " -> " in name:
            name = name.split(" -> ", 1)[0]
        if name in (".", ".."):
            return None
        size = int(parts[4]) if parts[4].isdigit() else None
        return RemoteEntry(name, parts[0].startswith("d"), size, " ".join(parts[5:8]))
    parts = line.split(None, 3)  # DOS: 01-02-24  10:00AM  <DIR>  name
    if len(parts) == 4:
        is_dir = parts[2].upper() == "<DIR>"
        size = None if is_dir or not parts[2].isdigit() else int(parts[2])
        return RemoteEntry(parts[3], is_dir, size, f"{parts[0]} {parts[1]}")
    return None


def human_size(n: Optional[int]) -> str:
    if n is None:
        return ""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return str(n)


def _text_progress(label: str) -> ProgressCallback:
    def cb(done: int, total: Optional[int]) -> None:
        if total:
            pct = done * 100 // total
            bar = "#" * (pct // 4)
            sys.stdout.write(f"\r{label}: [{bar:<25}] {pct:3d}% {human_size(done)}")
        else:
            sys.stdout.write(f"\r{label}: {human_size(done)}")
        sys.stdout.flush()
    return cb


# ---------------------------------------------------------------- CLI shell
class FTPShell(cmd.Cmd):
    intro = "More Simple FTP. Type 'help' for commands."

    def __init__(self, client: FTPClient) -> None:
        super().__init__()
        self.client = client
        self._update_prompt()

    def _update_prompt(self) -> None:
        try:
            where = self.client.pwd()
        except Exception:
            where = "?"
        self.prompt = f"ftp {self.client.host}:{where}> "

    def onecmd(self, line: str) -> bool:
        try:
            return super().onecmd(line)
        except ftplib.all_errors as exc:
            print(f"Error: {exc}")
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Error: {exc}")
        return False

    def postcmd(self, stop: bool, line: str) -> bool:
        if not stop:
            self._update_prompt()
        return stop

    def emptyline(self) -> bool:
        return False

    @staticmethod
    def _args(arg: str) -> List[str]:
        return shlex.split(arg, posix=True)

    def do_ls(self, arg: str) -> None:
        """ls [path]  - list a remote directory"""
        args = self._args(arg)
        for e in self.client.listdir(args[0] if args else ""):
            kind = "<DIR>" if e.is_dir else human_size(e.size)
            print(f"{e.modified:<17} {kind:>10}  {e.name}{'/' if e.is_dir else ''}")

    do_dir = do_ls

    def do_cd(self, arg: str) -> None:
        """cd <path>  - change remote directory"""
        args = self._args(arg)
        print(self.client.cd(args[0] if args else "/"))

    def do_pwd(self, arg: str) -> None:
        """pwd  - show the current remote directory"""
        print(self.client.pwd())

    def do_lcd(self, arg: str) -> None:
        """lcd [path]  - change local directory"""
        args = self._args(arg)
        if args:
            os.chdir(os.path.expanduser(args[0]))
        print(os.getcwd())

    def do_get(self, arg: str) -> None:
        """get <remote> [local]  - download a file"""
        args = self._args(arg)
        if not args:
            raise ValueError("usage: get <remote> [local]")
        local = self.client.get(args[0], args[1] if len(args) > 1 else None,
                                _text_progress("get " + args[0]))
        print(f"\nSaved to {local}")

    def do_put(self, arg: str) -> None:
        """put <local> [remote]  - upload a file"""
        args = self._args(arg)
        if not args:
            raise ValueError("usage: put <local> [remote]")
        remote = self.client.put(os.path.expanduser(args[0]),
                                 args[1] if len(args) > 1 else None,
                                 _text_progress("put " + args[0]))
        print(f"\nUploaded as {remote}")

    def do_mkdir(self, arg: str) -> None:
        """mkdir <path>  - create a remote directory"""
        args = self._args(arg)
        if not args:
            raise ValueError("usage: mkdir <path>")
        print(self.client.mkdir(args[0]))

    def do_rm(self, arg: str) -> None:
        """rm <file>  - delete a remote file"""
        args = self._args(arg)
        if not args:
            raise ValueError("usage: rm <file>")
        self.client.rm(args[0])
        print(f"Deleted {args[0]}")

    def do_rmdir(self, arg: str) -> None:
        """rmdir [-r] <dir>  - remove a remote directory (-r: with contents)"""
        args = self._args(arg)
        recursive = "-r" in args
        args = [a for a in args if a != "-r"]
        if not args:
            raise ValueError("usage: rmdir [-r] <dir>")
        self.client.rmdir(args[0], recursive=recursive)
        print(f"Removed {args[0]}")

    def do_rename(self, arg: str) -> None:
        """rename <old> <new>  - rename a remote file or directory"""
        args = self._args(arg)
        if len(args) != 2:
            raise ValueError("usage: rename <old> <new>")
        self.client.rename(args[0], args[1])

    def do_quit(self, arg: str) -> bool:
        """quit  - disconnect and exit"""
        self.client.close()
        return True

    do_exit = do_bye = do_quit

    def do_EOF(self, arg: str) -> bool:
        print()
        return self.do_quit(arg)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ftpclient", description="More Simple FTP command-line client")
    p.add_argument("host", nargs="?", help="FTP server host name, or the name of a saved connection")
    p.add_argument("command", nargs="*",
                   help="optional one-shot command, e.g. 'ls /pub' or 'get file.txt' (put -- before commands that take flags)")
    p.add_argument("-P", "--port", type=int, default=None, help="port (default 21)")
    p.add_argument("-u", "--user", default=None, help="user name (default anonymous)")
    p.add_argument("-p", "--password", help="password (prompted for if omitted and user is not anonymous)")
    p.add_argument("--tls", action="store_true", default=None, help="use explicit FTPS (FTP over TLS)")
    saved = p.add_argument_group("saved connections (shared with the MoreSimpleFTP window)")
    saved.add_argument("--save", metavar="NAME", help="after connecting, save these details as NAME")
    saved.add_argument("--save-password", action="store_true",
                       help="with --save: also keep the password in the system password store")
    saved.add_argument("--saved", action="store_true", help="list saved connections and exit")
    saved.add_argument("--forget", metavar="NAME", help="delete a saved connection and exit")
    p.add_argument("--active", action="store_true", help="use active mode instead of passive")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: Optional[Iterable[str]] = None, saved_connections=None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    from connections import Connection, SavedConnections  # local import keeps the core dependency-free
    store = saved_connections if saved_connections is not None else SavedConnections()

    if args.saved:
        conns = store.all()
        if not conns:
            print("No saved connections yet. Save one with --save NAME.")
        for c in conns:
            extra = (", TLS" if c.tls else "") + (", password saved" if c.save_password else "")
            print(f"{c.name}: {c.user}@{c.host}:{c.port}{extra}")
        return 0
    if args.forget:
        if store.remove(args.forget):
            print(f"Forgot {args.forget}")
            return 0
        print(f"No saved connection called {args.forget}", file=sys.stderr)
        return 1
    if not args.host:
        parser.error("give a host name or the name of a saved connection (see --saved)")

    saved = store.get(args.host)
    host = saved.host if saved else args.host
    port = args.port if args.port is not None else (saved.port if saved else 21)
    user = args.user if args.user is not None else (saved.user if saved else "anonymous")
    tls = bool(args.tls) or bool(saved and saved.tls)
    password = args.password
    if password is None and saved and user == saved.user:
        password = store.password(saved)
    if password is None:
        password = "" if user == "anonymous" else getpass.getpass(f"Password for {user}: ")
    client = FTPClient()
    try:
        print(client.connect(host, port, user, password, tls=tls, passive=not args.active))
    except ftplib.all_errors as exc:
        print(f"Could not connect: {exc}", file=sys.stderr)
        return 1
    if args.save:
        keep = bool(args.save_password and password)
        try:
            store.save(Connection(args.save, host, port, user, tls, save_password=keep),
                       password if keep else None)
            print(f"Saved as {args.save}" + (" (with password)" if keep else ""))
        except Exception as exc:
            print(f"Could not save the connection: {exc}", file=sys.stderr)
    elif saved:
        try:
            store.mark_used(saved.name)
        except OSError:
            pass
    shell = FTPShell(client)
    if args.command:
        shell.onecmd(shlex.join(args.command))
        client.close()
        return 0
    try:
        shell.cmdloop()
    except KeyboardInterrupt:
        print()
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
