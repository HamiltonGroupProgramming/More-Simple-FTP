#!/usr/bin/env python3
"""More Simple FTP - two-pane GUI (local files left, server files right) with switchable looks."""

from __future__ import annotations

import ftplib
import os
import posixpath
import queue
import random
import re
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, simpledialog, ttk
from typing import Callable, List, Optional, Tuple

from about import set_window_icon, show_about
from appsettings import load_settings, save_settings
from connections import Connection, SavedConnections, default_name
from ftpclient import FTPClient, RemoteEntry, __version__, human_size
from looks import LOOKS, Fonts, Look, apply_look, get_look

APP_NAME = "More Simple FTP"
PASSWORD_STORE = "Windows Credential Manager" if sys.platform == "win32" else "your system's password store"


class FilePane(ttk.Frame):
    """A titled file list with a path bar."""

    def __init__(self, master: tk.Misc, title: str, title_style: str,
                 on_open: Callable[[str], None], font=None, scale: float = 1.0) -> None:
        super().__init__(master, padding=4, style="Card.TFrame")
        self.on_open = on_open
        self.last: Tuple[str, List[RemoteEntry]] = ("", [])
        ttk.Label(self, text=title, style=title_style).pack(anchor="w")
        self.path_var = tk.StringVar()
        path_entry = ttk.Entry(self, textvariable=self.path_var, font=font)
        path_entry.pack(fill="x", pady=(2, 6))
        path_entry.bind("<Return>", lambda e: self.on_open(self.path_var.get()))

        body = ttk.Frame(self, style="Card.TFrame")
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, columns=("size", "modified"), selectmode="extended")
        self.tree.heading("#0", text="Name")
        self.tree.heading("size", text="Size")
        self.tree.heading("modified", text="Modified")
        self.tree.column("#0", width=120, minwidth=80)
        self.tree.column("size", width=int(70 * scale), anchor="e", stretch=False)
        self.tree.column("modified", width=int(130 * scale), stretch=False)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._double_click)
        self.tree.bind("<Return>", self._double_click)
        self.tree.bind("<BackSpace>", lambda e: self.on_open(".."))

    def show(self, path: str, entries: List[RemoteEntry]) -> None:
        self.last = (path, list(entries))
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


def rough_line(rng: random.Random, x0: float, y0: float, x1: float, y1: float,
               wobble: float = 2.5) -> List[float]:
    """Points along a line with a little hand-drawn wobble."""
    steps = max(int(max(abs(x1 - x0), abs(y1 - y0)) / 40), 2)
    pts: List[float] = []
    for i in range(steps + 1):
        t = i / steps
        jitter = 0 if i in (0, steps) else wobble
        pts += [x0 + (x1 - x0) * t + rng.uniform(-jitter, jitter),
                y0 + (y1 - y0) * t + rng.uniform(-jitter, jitter)]
    return pts


class Card(tk.Frame):
    """A box around part of the window: a hand-drawn crayon outline, a thin border, or nothing.

    Put children in ``.inner``.
    """

    def __init__(self, master: tk.Misc, look: Look, outline: Optional[str] = None,
                 fit_height: bool = False, pad: int = 10) -> None:
        c = look.colors or {}
        super().__init__(master, bg=c.get("bg"), highlightthickness=0, bd=0)
        self.look = look
        self.outline = outline or c.get("border", "black")
        self.pad = pad if look.colors else 0
        self.fit_height = fit_height
        self.seed = random.randrange(1 << 30)
        self.canvas: Optional[tk.Canvas] = None
        if look.sketchy:
            self.canvas = tk.Canvas(self, bg=c["bg"], highlightthickness=0, bd=0)
            self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
            self.bind("<Configure>", lambda e: self._draw())
        elif look.colors:
            self.configure(bg=c["panel"], highlightthickness=1, highlightbackground=c["border"],
                           highlightcolor=c["border"])
        self.inner = ttk.Frame(self, style="Card.TFrame" if look.colors else "TFrame")
        fill = "x" if fit_height else "both"
        self.inner.pack(fill=fill, expand=not fit_height, padx=self.pad, pady=self.pad)

    def _draw(self) -> None:
        cv = self.canvas
        if cv is None:
            return
        cv.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20 or h < 20:
            return
        rng = random.Random(self.seed)
        m = 4
        for width, shift in ((3, 0.0), (1.5, 1.5)):  # two passes, like going over it in crayon
            corners = [(m + rng.uniform(-2, 2) + shift, m + rng.uniform(-2, 2)),
                       (w - m + rng.uniform(-2, 2), m + rng.uniform(-2, 2) + shift),
                       (w - m + rng.uniform(-2, 2) - shift, h - m + rng.uniform(-2, 2)),
                       (m + rng.uniform(-2, 2), h - m + rng.uniform(-2, 2) - shift)]
            for i in range(4):
                (ax, ay), (bx, by) = corners[i], corners[(i + 1) % 4]
                # overshoot the corners a touch so it looks drawn by hand
                dx, dy = (bx - ax) * 0.01, (by - ay) * 0.01
                cv.create_line(*rough_line(rng, ax - dx, ay - dy, bx + dx, by + dy),
                               fill=self.outline, width=width, smooth=True, capstyle="round")


def squiggle(master: tk.Misc, color: str, bg: str, width: int = 260) -> tk.Canvas:
    """A crayon underline."""
    cv = tk.Canvas(master, width=width, height=12, bg=bg, highlightthickness=0, bd=0)
    rng = random.Random(7)
    pts: List[float] = []
    for i in range(9):
        pts += [4 + (width - 8) * i / 8, 6 + rng.uniform(-3, 3)]
    cv.create_line(*pts, fill=color, width=4, smooth=True, capstyle="round")
    return cv


class App(tk.Tk):
    def __init__(self, settings_file: Optional[str] = None) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {__version__}")
        self.geometry("1100x740")
        self.minsize(760, 460)
        set_window_icon(self)
        self.client = FTPClient()
        self.local_dir = os.path.expanduser("~")
        self.remote_dir = "/"
        self.busy = False
        self.ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self.settings_file = settings_file
        self.settings = load_settings(settings_file)
        self.system_theme = ttk.Style(self).theme_use()

        self.host_var = tk.StringVar()
        self.port_var = tk.StringVar(value="21")
        self.user_var = tk.StringVar(value="anonymous")
        self.pass_var = tk.StringVar()
        self.tls_var = tk.BooleanVar(value=False)
        self.active_side = tk.StringVar(value="remote")
        self.look_var = tk.StringVar(value=get_look(self.settings.get("look")).key)
        self.saved = SavedConnections(settings_file)
        self.saved_var = tk.StringVar()

        self.shell: Optional[tk.Frame] = None
        self._build_menu()
        self.build_ui()
        last = self.saved.last_used()
        if last:
            self.load_saved(last.name)
        self.refresh_local()
        self.bind_all("<F5>", lambda e: self.upload())
        self.bind_all("<F6>", lambda e: self.download())
        self.after(50, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------ looks
    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        settings = tk.Menu(menubar, tearoff=False)
        look_menu = tk.Menu(settings, tearoff=False)
        for key, look in LOOKS.items():
            look_menu.add_radiobutton(label=look.name, value=key, variable=self.look_var,
                                      command=self.change_look)
        settings.add_cascade(label="Look", menu=look_menu)
        menubar.add_cascade(label="Settings", menu=settings)
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="What's new", command=self.show_changelog)
        help_menu.add_command(label=f"About {APP_NAME}", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)

    def show_about(self) -> None:
        show_about(self, self.look, self.fonts, underline=squiggle)

    def show_changelog(self) -> None:
        if getattr(self, "changelog_win", None) is not None and self.changelog_win.winfo_exists():
            self.changelog_win.lift()
            return
        self.changelog_win = ChangelogWindow(self, self.look, self.fonts)

    def change_look(self) -> None:
        key = self.look_var.get()
        if key == self.look.key:
            return
        self.build_ui()
        self.settings = load_settings(self.settings_file)  # keep saved connections intact
        self.settings["look"] = key
        try:
            save_settings(self.settings, self.settings_file)
        except OSError as exc:
            self.write_log(f"Could not save settings: {exc}")
        self.write_log(f"Look changed to {self.look.name}.")

    def build_ui(self) -> None:
        """(Re)build every widget in the chosen look, keeping what's on screen."""
        old_log = ""
        old_local = old_remote = None
        if self.shell is not None:
            old_log = self.log.get("1.0", "end-1c")
            old_local, old_remote = self.local_pane.last, self.remote_pane.last
            self.shell.destroy()

        self.look = get_look(self.look_var.get())
        self.fonts: Fonts = apply_look(self, self.look, self.system_theme)
        c = self.look.colors or {}
        self.configure(bg=c.get("bg", ttk.Style(self).lookup("TFrame", "background") or None))
        self.option_add("*TCombobox*Listbox.font", self.fonts.body)

        self.shell = ttk.Frame(self, padding=(14, 10) if self.look.colors else 0)
        self.shell.pack(fill="both", expand=True)
        if self.look.header:
            self._build_header()
        self._build_connect_bar()
        if self.look.actions == "top":
            self._build_toolbar()
        # bottom-anchored parts go in before the panes so the panes give up space, not them
        self._build_status()
        if self.look.actions == "bottom":
            self._build_toolbar()
        self._build_panes()

        if old_local is not None:
            self.local_pane.show(*old_local)
            self.remote_pane.show(*old_remote)
            self.log.configure(state="normal")
            self.log.insert("end", old_log)
            self.log.see("end")
            self.log.configure(state="disabled")
        self._update_connected()

    # ------------------------------------------------------------ layout
    def _build_header(self) -> None:
        look, c = self.look, self.look.colors or {}
        bar = ttk.Frame(self.shell)
        bar.pack(fill="x", pady=(0, 8))
        title = ttk.Frame(bar)
        title.pack(side="left")
        ttk.Label(title, text=APP_NAME, style="Header.TLabel").pack(anchor="w")
        if look.sketchy:
            squiggle(title, c["accent2"], c["bg"]).pack(anchor="w")
        self.status_label = ttk.Label(bar, style="Status.TLabel")
        self.status_label.pack(side="right")

    def _build_connect_bar(self) -> None:
        look = self.look
        card = Card(self.shell, look, fit_height=True, pad=8)
        card.pack(fill="x", pady=(0, 8) if look.colors else (6, 0), padx=0 if look.colors else 6)
        self._build_saved_row(card.inner)
        bar = ttk.Frame(card.inner, style="Card.TFrame" if look.colors else "TFrame")
        bar.pack(fill="x")
        self.connect_btn = ttk.Button(bar, text=look.label("connect"), command=self.toggle_connection,
                                      style="Primary.TButton" if look.colors else "TButton")
        if look.colors:
            self.connect_btn.pack(side="right", padx=4)
        shrink = min(1.0, 9 / look.size)
        for key, var, width, show in (("host", self.host_var, 24, ""), ("port", self.port_var, 6, ""),
                                       ("user", self.user_var, 14, ""), ("password", self.pass_var, 14, "•")):
            width = max(int(width * shrink), 5)
            ttk.Label(bar, text=look.label(key), style="Card.TLabel" if look.colors else "TLabel",
                      font=self.fonts.body).pack(side="left", padx=(6, 3))
            entry = ttk.Entry(bar, textvariable=var, width=width, show=show, font=self.fonts.body)
            entry.pack(side="left")
            entry.bind("<Return>", lambda e: self.connect())
            if key == "password":
                self.pass_entry = entry
        ttk.Checkbutton(bar, text=look.label("tls"), variable=self.tls_var,
                        style="Card.TCheckbutton").pack(side="left", padx=8)
        if not look.colors:
            self.connect_btn.pack(side="left", padx=4)
        else:
            ttk.Style(self).configure("Card.TCheckbutton", background=look.colors["panel"])

    def _build_saved_row(self, parent: tk.Misc) -> None:
        """Saved connections: pick one to fill in the boxes, or save/forget the current one."""
        look = self.look
        styled = bool(look.colors)
        row = ttk.Frame(parent, style="Card.TFrame" if styled else "TFrame")
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text=look.label("saved"), style="Card.TLabel" if styled else "TLabel",
                  font=self.fonts.body).pack(side="left", padx=(6, 3))
        self.saved_box = ttk.Combobox(row, textvariable=self.saved_var, state="readonly",
                                      width=max(int(28 * min(1.0, 10 / look.size)), 16),
                                      font=self.fonts.body)
        self.saved_box.pack(side="left")
        self.saved_box.bind("<<ComboboxSelected>>", lambda e: self.load_saved(self.saved_var.get()))
        ttk.Button(row, text=look.label("save_conn"), command=self.save_current,
                   style="TButton").pack(side="left", padx=(8, 4))
        self.forget_btn = ttk.Button(row, text=look.label("forget_conn"), command=self.forget_saved,
                                     style="Danger.TButton" if styled else "TButton")
        self.forget_btn.pack(side="left", padx=4)
        self._refresh_saved_list()

    def _refresh_saved_list(self) -> None:
        names = self.saved.names()
        self.saved_box.configure(values=names)
        if self.saved_var.get() not in names:
            self.saved_var.set("")
        self.forget_btn.state(["!disabled"] if self.saved_var.get() else ["disabled"])

    # ------------------------------------------------------------ saved connections
    def load_saved(self, name: str) -> None:
        conn = self.saved.get(name)
        if conn is None:
            return
        self.saved_var.set(conn.name)
        self.host_var.set(conn.host)
        self.port_var.set(str(conn.port))
        self.user_var.set(conn.user)
        self.tls_var.set(conn.tls)
        self.pass_var.set(self.saved.password(conn) or "")
        self._refresh_saved_list()
        if not self.pass_var.get() and conn.user != "anonymous":
            self.after_idle(self.pass_entry.focus_set)

    def save_current(self) -> None:
        host = self.host_var.get().strip()
        if not host:
            messagebox.showwarning(APP_NAME, "Fill in the server first, then save it.")
            return
        try:
            port = int(self.port_var.get() or 21)
        except ValueError:
            messagebox.showwarning(APP_NAME, "Port must be a number.")
            return
        user = self.user_var.get().strip() or "anonymous"
        current = self.saved.get(self.saved_var.get()) if self.saved_var.get() else None
        suggested = current.name if current and current.host == host else default_name(host, user)
        dialog = SaveDialog(self, self.look, suggested,
                            has_password=bool(self.pass_var.get()),
                            can_store=self.saved.can_save_passwords,
                            remember=current.save_password if current else True)
        if not dialog.result:
            return
        name, remember = dialog.result
        existing = self.saved.get(name)
        if existing and (current is None or existing.name.lower() != current.name.lower()):
            if not messagebox.askyesno(APP_NAME, f"Replace the saved connection '{existing.name}'?"):
                return
        conn = Connection(name=existing.name if existing else name, host=host, port=port, user=user,
                          tls=self.tls_var.get(), save_password=remember and bool(self.pass_var.get()))
        try:
            self.saved.save(conn, self.pass_var.get() if conn.save_password else None)
        except Exception as exc:  # e.g. password store refused
            messagebox.showerror(APP_NAME, f"Could not save: {exc}")
            return
        self.saved_var.set(conn.name)
        self._refresh_saved_list()
        extra = f" (password kept in {PASSWORD_STORE})" if conn.save_password else ""
        self.write_log(f"Saved '{conn.name}'{extra}.")

    def forget_saved(self) -> None:
        name = self.saved_var.get()
        if not name or not messagebox.askyesno(APP_NAME, f"Forget the saved connection '{name}'?"):
            return
        try:
            self.saved.remove(name)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not forget it: {exc}")
            return
        self.saved_var.set("")
        self._refresh_saved_list()
        self.write_log(f"Forgot '{name}'.")

    def _action_buttons(self, parent: tk.Misc, side: Optional[str],
                        file_parent: Optional[tk.Misc] = None) -> None:
        """Upload/download plus the file actions, stacked (side=None) or in a row.

        ``file_parent`` puts the file actions in a separate row.
        """
        look = self.look
        styled = bool(look.colors)

        def btn(key: str, cmd: Callable[[], None], style: str = "TButton") -> ttk.Button:
            nonlocal parent
            b = ttk.Button(parent, text=look.label(key), command=cmd, style=style if styled else "TButton")
            if side:
                b.pack(side=side, padx=(0, 8))
            else:
                b.pack(fill="x", pady=4)
            return b

        btn("upload", self.upload, "Primary.TButton")
        btn("download", self.download, "Secondary.TButton")
        if file_parent is not None:
            parent = file_parent
        elif side:
            ttk.Separator(parent, orient="vertical").pack(side=side, fill="y", padx=(4, 12), pady=4)
        else:
            ttk.Separator(parent).pack(fill="x", pady=8)
        btn("new_folder", self.new_folder)
        btn("rename", self.rename)
        btn("delete", self.delete, "Danger.TButton")
        btn("refresh", self.refresh_all)

    def _side_picker(self, parent: tk.Misc, row: bool) -> None:
        look = self.look
        if row:
            f = ttk.Frame(parent)
            f.pack(side="right")
            ttk.Label(f, text=look.label("apply_to").replace("\n", " "),
                      style="Muted.TLabel").pack(side="left", padx=(8, 4))
            ttk.Radiobutton(f, text=look.label("local"), value="local",
                            variable=self.active_side).pack(side="left", padx=4)
            ttk.Radiobutton(f, text=look.label("remote"), value="remote",
                            variable=self.active_side).pack(side="left", padx=4)
            if look.label("hint"):
                ttk.Label(parent, text=look.label("hint"), style="Muted.TLabel").pack(side="right", padx=12)
        else:
            ttk.Label(parent, text=look.label("apply_to"), justify="center").pack(pady=(10, 2))
            ttk.Radiobutton(parent, text=look.label("remote"), value="remote",
                            variable=self.active_side).pack(anchor="w")
            ttk.Radiobutton(parent, text=look.label("local"), value="local",
                            variable=self.active_side).pack(anchor="w")

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.shell)
        top = self.look.actions == "top"
        if top:
            bar.pack(fill="x", pady=(0, 8))
        else:
            bar.pack(side="bottom", fill="x", pady=(8, 0))
        if self.look.toolbar_rows == 2:
            first, second = ttk.Frame(bar), ttk.Frame(bar)
            first.pack(fill="x")
            second.pack(fill="x", pady=(8, 0))
            self._action_buttons(first, side="left", file_parent=second)
            self._side_picker(first, row=True)
        else:
            self._action_buttons(bar, side="left")
            self._side_picker(bar, row=True)

    def _build_panes(self) -> None:
        look = self.look
        c = look.colors or {}
        paned = ttk.PanedWindow(self.shell, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=0 if look.colors else 6, pady=0 if look.colors else 6)

        local_card = Card(paned, look, outline=c.get("local"))
        remote_card = Card(paned, look, outline=c.get("remote"))
        font, scale = self.fonts.body, max(look.size / 10, 1.0)
        self.local_pane = FilePane(local_card.inner, look.label("local"), "LocalTitle.TLabel",
                                   self.open_local, font, scale)
        self.remote_pane = FilePane(remote_card.inner, look.label("remote"), "RemoteTitle.TLabel",
                                    self.open_remote, font, scale)
        self.local_pane.pack(fill="both", expand=True)
        self.remote_pane.pack(fill="both", expand=True)

        paned.add(local_card, weight=1)
        if look.actions == "middle":
            middle = ttk.Frame(paned, padding=(10, 6) if look.colors else (4, 40))
            self._action_buttons(middle, side=None)
            self._side_picker(middle, row=False)
            paned.add(middle, weight=0)
        else:
            paned.add(ttk.Frame(paned, width=12), weight=0)  # a gap between the two cards
        paned.add(remote_card, weight=1)

        for pane, side in ((self.local_pane, "local"), (self.remote_pane, "remote")):
            pane.tree.bind("<FocusIn>", lambda e, s=side: self.active_side.set(s), add="+")
            pane.tree.bind("<Delete>", lambda e: self.delete())

    def _build_status(self) -> None:
        look = self.look
        c = look.colors or {}
        bottom = ttk.Frame(self.shell, padding=(0, 8, 0, 0) if look.colors else (6, 0, 6, 6))
        bottom.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        self.log = tk.Text(bottom, height=look.log_lines, state="disabled", wrap="word")
        if look.colors:
            self.log.configure(bg=c.get("log_bg", c["field"]), fg=c["muted"], font=self.fonts.small,
                               relief="flat", highlightthickness=1, highlightbackground=c["border"],
                               insertbackground=c["ink"], padx=8, pady=6)
        self.log.pack(fill="x", pady=(6, 0))

    def _update_connected(self) -> None:
        on = self.client.connected
        self.connect_btn.configure(text=self.look.label("disconnect" if on else "connect"))
        if self.look.header:
            text = self.look.label("status_on" if on else "status_off")
            if on and self.host_var.get():
                text += f"  {self.host_var.get().strip()}"
            self.status_label.configure(text=text)
            if self.look.colors:
                self.status_label.configure(foreground=self.look.colors["ok" if on else "muted"])

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
            chosen = self.saved.get(self.saved_var.get()) if self.saved_var.get() else None
            if chosen and chosen.host == host:
                try:
                    self.saved.mark_used(chosen.name)
                except OSError:
                    pass
            self._update_connected()
            self.remote_dir = cwd
            self.remote_pane.show(cwd, entries)

        self.run_bg("Connect", work, done)

    def disconnect(self) -> None:
        self.client.close()
        self._update_connected()
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


def changelog_text() -> str:
    """CHANGELOG.md, next to this file or bundled inside the .exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(base, "CHANGELOG.md"), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return f"# What's new\n\nYou're running version {__version__}."


class ChangelogWindow(tk.Toplevel):
    """Shows CHANGELOG.md with headings and bullets tidied up, in the current look."""

    def __init__(self, parent: tk.Misc, look: Look, fonts: Fonts) -> None:
        super().__init__(parent)
        self.title(f"What's new in {APP_NAME}")
        self.geometry("620x520")
        self.transient(parent)
        c = look.colors or {}
        bg, ink = c.get("panel"), c.get("ink")
        if c:
            self.configure(bg=c["bg"])
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Button(frame, text="Close", command=self.destroy,
                   style="Primary.TButton" if c else "TButton").pack(side="bottom", anchor="e", pady=(8, 0))
        text = tk.Text(frame, wrap="word", relief="flat", padx=14, pady=10, cursor="arrow",
                       highlightthickness=1 if c else 0)
        if c:
            text.configure(bg=bg, fg=ink, highlightbackground=c["border"], highlightcolor=c["border"])
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)

        base = tkfont.Font(font=fonts.body)
        fam, size = base.actual("family"), base.actual("size")
        text.configure(font=(fam, size))
        title_color = c.get("title", ink) if c else None
        text.tag_configure("h1", font=(fam, size + 8, "bold"), foreground=title_color, spacing3=8)
        text.tag_configure("h2", font=(fam, size + 4, "bold"), foreground=title_color,
                           spacing1=14, spacing3=4)
        text.tag_configure("h3", font=(fam, size + 1, "bold"), spacing1=8, spacing3=2)
        text.tag_configure("bullet", lmargin1=10, lmargin2=28, spacing3=3)
        text.tag_configure("bold", font=(fam, size, "bold"))
        text.tag_configure("code", font=("Consolas", size) if sys.platform == "win32" else ("Courier", size))
        for line in changelog_text().splitlines():
            if line.startswith("### "):
                text.insert("end", line[4:] + "\n", "h3")
            elif line.startswith("## "):
                text.insert("end", line[3:] + "\n", "h2")
            elif line.startswith("# "):
                text.insert("end", line[2:] + "\n", "h1")
            elif line.startswith("- "):
                text.insert("end", "•  ", "bullet")
                self._inline(text, line[2:], "bullet")
                text.insert("end", "\n", "bullet")
            else:
                self._inline(text, line, ())
                text.insert("end", "\n")
        text.configure(state="disabled")
        self.bind("<Escape>", lambda e: self.destroy())

    @staticmethod
    def _inline(text: tk.Text, line: str, base_tag) -> None:
        """Very small Markdown: **bold** and `code`."""
        base = (base_tag,) if isinstance(base_tag, str) else tuple(base_tag)
        for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", line):
            if part.startswith("**") and part.endswith("**") and len(part) > 4:
                text.insert("end", part[2:-2], base + ("bold",))
            elif part.startswith("`") and part.endswith("`") and len(part) > 2:
                text.insert("end", part[1:-1], base + ("code",))
            elif part:
                text.insert("end", part, base)


class SaveDialog(simpledialog.Dialog):
    """Ask for a name for the connection and whether to remember the password."""

    def __init__(self, parent: tk.Misc, look: Look, name: str, has_password: bool,
                 can_store: bool, remember: bool) -> None:
        self.look = look
        self.name_var = tk.StringVar(value=name)
        self.remember_var = tk.BooleanVar(value=remember and has_password and can_store)
        self.has_password, self.can_store = has_password, can_store
        self.result: Optional[Tuple[str, bool]] = None
        super().__init__(parent, title="Save connection")

    def body(self, master: tk.Frame) -> tk.Widget:
        bg = (self.look.colors or {}).get("bg")
        if bg:
            self.configure(bg=bg)
            master.configure(bg=bg)
        frame = ttk.Frame(master, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Name for this connection:").pack(anchor="w")
        entry = ttk.Entry(frame, textvariable=self.name_var, width=34)
        entry.pack(fill="x", pady=(2, 10))
        entry.select_range(0, "end")
        check = ttk.Checkbutton(frame, text="Remember the password", variable=self.remember_var)
        check.pack(anchor="w")
        if not self.can_store:
            check.state(["disabled"])
            note = "Passwords can't be saved on this computer; you'll type it each time."
        elif not self.has_password:
            check.state(["disabled"])
            note = "No password typed in, so there is none to remember."
        else:
            note = f"It's kept in {PASSWORD_STORE}, not in a plain file."
        ttk.Label(frame, text=note, style="Muted.TLabel" if self.look.colors else "TLabel",
                  wraplength=300).pack(anchor="w", pady=(4, 0))
        return entry

    def buttonbox(self) -> None:
        box = ttk.Frame(self, padding=(10, 0, 10, 10))
        box.pack(fill="x")
        ttk.Button(box, text="Cancel", command=self.cancel).pack(side="right")
        ttk.Button(box, text="Save", command=self.ok,
                   style="Primary.TButton" if self.look.colors else "TButton").pack(side="right", padx=6)
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

    def validate(self) -> bool:
        if not self.name_var.get().strip():
            messagebox.showwarning(APP_NAME, "Give the connection a name.", parent=self)
            return False
        return True

    def apply(self) -> None:
        self.result = (self.name_var.get().strip(), bool(self.remember_var.get()))


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
