#!/usr/bin/env python3
"""More Simple FTP - classic two-pane GUI (local files left, server files right)."""

from __future__ import annotations

import ftplib
import os
import posixpath
import queue
import shutil
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Callable, List, Optional

from ftpclient import FTPClient, RemoteEntry, __version__, human_size

APP_NAME = "More Simple FTP"


class FilePane(ttk.Frame):
    """A titled file list with a path bar."""

    def __init__(self, master: tk.Misc, title: str, on_open: Callable[[str], None]) -> None:
        super().__init__(master, padding=4)
        self.on_open = on_open
        ttk.Label(self, text=title, font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.path_var = tk.StringVar()
        path_entry = ttk.Entry(self, textvariable=self.path_var)
        path_entry.pack(fill="x", pady=(2, 4))
        path_entry.bind("<Return>", lambda e: self.on_open(self.path_var.get()))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, columns=("size", "modified"), selectmode="extended")
        self.tree.heading("#0", text="Name")
        self.tree.heading("size", text="Size")
        self.tree.heading("modified", text="Modified")
        self.tree.column("#0", width=220)
        self.tree.column("size", width=80, anchor="e")
        self.tree.column("modified", width=140)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._double_click)
        self.tree.bind("<Return>", self._double_click)
        self.tree.bind("<BackSpace>", lambda e: self.on_open(".."))

    def show(self, path: str, entries: List[RemoteEntry]) -> None:
        self.path_var.set(path)
        self.tree.delete(*self.tree.get_children())
        self.tree.insert("", "end", iid="..", text="📁 ..", values=("", ""))
        for e in entries:
            icon = "📁" if e.is_dir else "📄"
            self.tree.insert("", "end", iid="f:" + e.name, text=f"{icon} {e.name}",
                             values=("<DIR>" if e.is_dir else human_size(e.size), e.modified),
                             tags=("dir",) if e.is_dir else ())

    def selected(self) -> List[tuple]:
        """Return (name, is_dir) for every selected row except '..'."""
        out = []
        for iid in self.tree.selection():
            if iid == "..":
                continue
            out.append((iid[2:], "dir" in self.tree.item(iid, "tags")))
        return out

    def _double_click(self, _event: tk.Event) -> None:
        sel = self.tree.focus()
        if not sel:
            return
        if sel == "..":
            self.on_open("..")
        elif "dir" in self.tree.item(sel, "tags"):
            self.on_open(sel[2:])


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {__version__}")
        self.geometry("1000x640")
        self.minsize(720, 420)
        self.client = FTPClient()
        self.local_dir = os.path.expanduser("~")
        self.remote_dir = "/"
        self.busy = False
        self.ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()

        self._build_connect_bar()
        self._build_panes()
        self._build_status()
        self.refresh_local()
        self.after(50, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------ layout
    def _build_connect_bar(self) -> None:
        bar = ttk.Frame(self, padding=(6, 6, 6, 0))
        bar.pack(fill="x")
        self.host_var = tk.StringVar()
        self.port_var = tk.StringVar(value="21")
        self.user_var = tk.StringVar(value="anonymous")
        self.pass_var = tk.StringVar()
        self.tls_var = tk.BooleanVar(value=False)
        for label, var, width, show in (("Host", self.host_var, 24, ""), ("Port", self.port_var, 6, ""),
                                         ("User", self.user_var, 14, ""), ("Password", self.pass_var, 14, "•")):
            ttk.Label(bar, text=label).pack(side="left", padx=(6, 2))
            entry = ttk.Entry(bar, textvariable=var, width=width, show=show)
            entry.pack(side="left")
            entry.bind("<Return>", lambda e: self.connect())
        ttk.Checkbutton(bar, text="TLS", variable=self.tls_var).pack(side="left", padx=6)
        self.connect_btn = ttk.Button(bar, text="Connect", command=self.toggle_connection)
        self.connect_btn.pack(side="left", padx=4)

    def _build_panes(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6, pady=6)

        self.local_pane = FilePane(paned, "This computer", self.open_local)
        self.remote_pane = FilePane(paned, "Server", self.open_remote)

        middle = ttk.Frame(paned, padding=(4, 40))
        ttk.Button(middle, text="Upload →", command=self.upload).pack(fill="x", pady=4)
        ttk.Button(middle, text="← Download", command=self.download).pack(fill="x", pady=4)
        ttk.Separator(middle).pack(fill="x", pady=10)
        ttk.Button(middle, text="New folder", command=self.new_folder).pack(fill="x", pady=4)
        ttk.Button(middle, text="Rename", command=self.rename).pack(fill="x", pady=4)
        ttk.Button(middle, text="Delete", command=self.delete).pack(fill="x", pady=4)
        ttk.Button(middle, text="Refresh", command=self.refresh_all).pack(fill="x", pady=4)
        self.active_side = tk.StringVar(value="remote")
        ttk.Label(middle, text="Folder/rename/delete\napply to:").pack(pady=(14, 2))
        ttk.Radiobutton(middle, text="Server", value="remote", variable=self.active_side).pack(anchor="w")
        ttk.Radiobutton(middle, text="This computer", value="local", variable=self.active_side).pack(anchor="w")

        paned.add(self.local_pane, weight=1)
        paned.add(middle, weight=0)
        paned.add(self.remote_pane, weight=1)

        self.local_pane.tree.bind("<FocusIn>", lambda e: self.active_side.set("local"), add="+")
        self.remote_pane.tree.bind("<FocusIn>", lambda e: self.active_side.set("remote"), add="+")
        self.local_pane.tree.bind("<Delete>", lambda e: self.delete())
        self.remote_pane.tree.bind("<Delete>", lambda e: self.delete())

    def _build_status(self) -> None:
        bottom = ttk.Frame(self, padding=(6, 0, 6, 6))
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        self.log = tk.Text(bottom, height=6, state="disabled", wrap="word")
        self.log.pack(fill="x", pady=(4, 0))

    # ------------------------------------------------------------ threading
    def _drain_queue(self) -> None:
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.after(50, self._drain_queue)

    def ui(self, fn: Callable[[], None]) -> None:
        self.ui_queue.put(fn)

    def run_bg(self, label: str, work: Callable[[], object],
               done: Optional[Callable[[object], None]] = None) -> None:
        """Run network work off the UI thread, one job at a time."""
        if self.busy:
            self.write_log("Please wait for the current task to finish.")
            return
        self.busy = True
        self.config(cursor="watch")

        def runner() -> None:
            try:
                result = work()
            except ftplib.all_errors + (RuntimeError,) as exc:
                msg = f"{label} failed: {exc}"
                self.ui(lambda: self.write_log(msg))
                result = exc
                ok = False
            else:
                ok = True
            def finish() -> None:
                self.busy = False
                self.config(cursor="")
                self.progress["value"] = 0
                if ok and done:
                    done(result)
            self.ui(finish)

        threading.Thread(target=runner, daemon=True).start()

    def progress_cb(self, done: int, total: Optional[int]) -> None:
        if total:
            pct = done * 100 / total
            self.ui(lambda: self.progress.configure(value=pct))

    def write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------ connection
    def toggle_connection(self) -> None:
        if self.client.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self) -> None:
        host = self.host_var.get().strip()
        if not host:
            messagebox.showwarning(APP_NAME, "Enter a host name first.")
            return
        try:
            port = int(self.port_var.get() or 21)
        except ValueError:
            messagebox.showwarning(APP_NAME, "Port must be a number.")
            return
        user, password, tls = self.user_var.get().strip(), self.pass_var.get(), self.tls_var.get()
        self.write_log(f"Connecting to {host}:{port}…")

        def work():
            welcome = self.client.connect(host, port, user, password, tls=tls)
            return welcome, self.client.pwd(), self.client.listdir()

        def done(result):
            welcome, cwd, entries = result
            self.write_log(welcome)
            self.connect_btn.configure(text="Disconnect")
            self.remote_dir = cwd
            self.remote_pane.show(cwd, entries)

        self.run_bg("Connect", work, done)

    def disconnect(self) -> None:
        self.client.close()
        self.connect_btn.configure(text="Connect")
        self.remote_pane.show("", [])
        self.write_log("Disconnected.")

    def _on_close(self) -> None:
        self.client.close()
        self.destroy()

    # ------------------------------------------------------------ local side
    def refresh_local(self) -> None:
        entries: List[RemoteEntry] = []
        try:
            with os.scandir(self.local_dir) as it:
                for d in it:
                    try:
                        is_dir = d.is_dir()
                        st = d.stat()
                    except OSError:
                        continue
                    entries.append(RemoteEntry(d.name, is_dir, None if is_dir else st.st_size,
                                               time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))))
        except OSError as exc:
            self.write_log(f"Cannot read {self.local_dir}: {exc}")
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        self.local_pane.show(self.local_dir, entries)

    def open_local(self, name: str) -> None:
        target = os.path.normpath(os.path.join(self.local_dir, os.path.expanduser(name)))
        if os.path.isdir(target):
            self.local_dir = target
            self.refresh_local()
        else:
            self.local_pane.path_var.set(self.local_dir)

    # ------------------------------------------------------------ remote side
    def refresh_remote(self) -> None:
        if not self.client.connected:
            return
        self.run_bg("List", lambda: (self.client.pwd(), self.client.listdir()),
                    lambda r: self.remote_pane.show(*r))

    def open_remote(self, name: str) -> None:
        if not self.client.connected:
            return
        def work():
            cwd = self.client.cd(name)
            return cwd, self.client.listdir()
        def done(r):
            self.remote_dir = r[0]
            self.remote_pane.show(*r)
        self.run_bg("Change folder", work, done)

    def refresh_all(self) -> None:
        self.refresh_local()
        self.refresh_remote()

    # ------------------------------------------------------------ actions
    def _need_connection(self) -> bool:
        if not self.client.connected:
            messagebox.showinfo(APP_NAME, "Connect to a server first.")
            return False
        return True

    def upload(self) -> None:
        if not self._need_connection():
            return
        files = [(n, d) for n, d in self.local_pane.selected()]
        if not files:
            self.write_log("Select files on the left to upload.")
            return
        local_dir = self.local_dir

        def work():
            for name, is_dir in files:
                self._upload_path(os.path.join(local_dir, name), name, is_dir)
            return self.client.pwd(), self.client.listdir()

        self.run_bg("Upload", work, lambda r: self.remote_pane.show(*r))

    def _upload_path(self, local: str, remote: str, is_dir: bool) -> None:
        if is_dir:
            try:
                self.client.mkdir(remote)
            except ftplib.error_perm:
                pass  # already exists
            for child in os.listdir(local):
                path = os.path.join(local, child)
                self._upload_path(path, posixpath.join(remote, child), os.path.isdir(path))
        else:
            self.ui(lambda: self.write_log(f"Uploading {remote}…"))
            self.client.put(local, remote, self.progress_cb)

    def download(self) -> None:
        if not self._need_connection():
            return
        files = self.remote_pane.selected()
        if not files:
            self.write_log("Select files on the right to download.")
            return
        local_dir = self.local_dir

        def work():
            for name, is_dir in files:
                self._download_path(name, os.path.join(local_dir, name), is_dir)

        self.run_bg("Download", work, lambda _r: self.refresh_local())

    def _download_path(self, remote: str, local: str, is_dir: bool) -> None:
        if is_dir:
            os.makedirs(local, exist_ok=True)
            for e in self.client.listdir(remote):
                self._download_path(posixpath.join(remote, e.name), os.path.join(local, e.name), e.is_dir)
        else:
            self.ui(lambda: self.write_log(f"Downloading {remote}…"))
            self.client.get(remote, local, self.progress_cb)

    def new_folder(self) -> None:
        name = simpledialog.askstring(APP_NAME, "New folder name:", parent=self)
        if not name:
            return
        if self.active_side.get() == "local":
            try:
                os.mkdir(os.path.join(self.local_dir, name))
            except OSError as exc:
                self.write_log(f"New folder failed: {exc}")
            self.refresh_local()
        elif self._need_connection():
            self.run_bg("New folder", lambda: (self.client.mkdir(name), self.client.pwd(),
                                               self.client.listdir()),
                        lambda r: self.remote_pane.show(r[1], r[2]))

    def rename(self) -> None:
        local = self.active_side.get() == "local"
        sel = (self.local_pane if local else self.remote_pane).selected()
        if len(sel) != 1:
            self.write_log("Select exactly one item to rename.")
            return
        old = sel[0][0]
        new = simpledialog.askstring(APP_NAME, f"Rename '{old}' to:", initialvalue=old, parent=self)
        if not new or new == old:
            return
        if local:
            try:
                os.rename(os.path.join(self.local_dir, old), os.path.join(self.local_dir, new))
            except OSError as exc:
                self.write_log(f"Rename failed: {exc}")
            self.refresh_local()
        elif self._need_connection():
            self.run_bg("Rename", lambda: (self.client.rename(old, new), self.client.pwd(),
                                           self.client.listdir()),
                        lambda r: self.remote_pane.show(r[1], r[2]))

    def delete(self) -> None:
        local = self.active_side.get() == "local"
        sel = (self.local_pane if local else self.remote_pane).selected()
        if not sel:
            return
        where = "this computer" if local else "the server"
        names = ", ".join(n for n, _ in sel[:5]) + ("…" if len(sel) > 5 else "")
        if not messagebox.askyesno(APP_NAME, f"Permanently delete from {where}?\n\n{names}"):
            return
        if local:
            for name, is_dir in sel:
                path = os.path.join(self.local_dir, name)
                try:
                    shutil.rmtree(path) if is_dir else os.remove(path)
                except OSError as exc:
                    self.write_log(f"Delete failed: {exc}")
            self.refresh_local()
        elif self._need_connection():
            def work():
                for name, is_dir in sel:
                    if is_dir:
                        self.client.rmdir(name, recursive=True)
                    else:
                        self.client.rm(name)
                return self.client.pwd(), self.client.listdir()
            self.run_bg("Delete", work, lambda r: self.remote_pane.show(*r))


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
