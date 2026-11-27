"""The looks (themes) More Simple FTP can wear, and how to apply them to Tk/ttk.

A Look is plain data (colours, fonts, wording, layout choices). ``apply_look`` turns
it into a ttk theme so every widget picks it up; the GUI rebuilds itself on a change.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from tkinter import font as tkfont
from tkinter import ttk
from typing import Dict, Optional, Sequence, Tuple

# Words the window uses. A look can override any of them.
DEFAULT_LABELS: Dict[str, str] = {
    "host": "Host", "port": "Port", "user": "User", "password": "Password", "tls": "TLS",
    "connect": "Connect", "disconnect": "Disconnect",
    "local": "This computer", "remote": "Server",
    "upload": "Upload →", "download": "← Download",
    "new_folder": "New folder", "rename": "Rename", "delete": "Delete", "refresh": "Refresh",
    "apply_to": "Folder/rename/delete\napply to:",
    "status_on": "", "status_off": "", "hint": "",
}


@dataclass(frozen=True)
class Look:
    key: str
    name: str
    fonts: Sequence[str] = ()            # first installed family wins; empty = Tk default
    size: int = 10
    title_size: int = 18
    colors: Optional[Dict[str, str]] = None   # None = leave the system theme alone
    labels: Dict[str, str] = field(default_factory=dict)
    actions: str = "middle"              # where the action buttons sit: middle | top | bottom
    header: bool = True                  # app name + connection status across the top
    sketchy: bool = False                # hand-drawn crayon outlines around the panes
    row_height: int = 0                  # file list row height in px (0 = from font)
    pad: Tuple[int, int] = (10, 5)       # button padding (x, y)
    big_pad: Tuple[int, int] = (10, 5)   # padding for the upload/download buttons
    log_lines: int = 6
    toolbar_rows: int = 1                # 2 = file actions get their own row

    def label(self, key: str) -> str:
        return self.labels.get(key, DEFAULT_LABELS[key])


LOOKS: Dict[str, Look] = {}


def _add(look: Look) -> None:
    LOOKS[look.key] = look


_add(Look(
    key="crayon", name='Crayon "Simple"',
    fonts=("Patrick Hand", "Gochi Hand", "Comic Sans MS", "Comic Neue", "Chalkboard SE"),
    size=13, title_size=26, sketchy=True, row_height=30, pad=(12, 6), big_pad=(14, 10), log_lines=4,
    colors=dict(bg="#fdf8ec", panel="#fdf8ec", ink="#2a2a2a", muted="#555555", field="#fffdf5",
                border="#2a2a2a", select_bg="#fff3bf", select_fg="#2a2a2a",
                button="#fff3bf", button_fg="#2a2a2a", button_active="#ffec99",
                primary="#8ce99a", primary_fg="#2a2a2a", primary_active="#69db7c",
                secondary="#a5d8ff", secondary_fg="#2a2a2a", secondary_active="#74c0fc",
                danger="#ffe3e3", danger_fg="#a51d1d", danger_active="#ffc9c9",
                progress="#51cf66", trough="#ffffff", local="#2b59c3", remote="#d9480f",
                title="#2b59c3", accent2="#f06595", ok="#2f9e44", heading="#fdf8ec"),
    labels={"host": "Server", "user": "Name", "tls": "Lock it (TLS)",
            "connect": "Let's go!", "disconnect": "Hang up",
            "local": "My computer", "remote": "The server",
            "upload": "Send it →", "download": "← Get it", "delete": "Throw away",
            "refresh": "Look again", "apply_to": "Folder, rename and\nthrow away work on:",
            "status_on": "connected!", "status_off": "not connected yet"},
))

_add(Look(
    key="modern", name="Clean modern",
    fonts=("Segoe UI", "Inter", "Helvetica Neue", "Helvetica", "DejaVu Sans"),
    size=10, title_size=13, actions="top", row_height=30, pad=(12, 6), big_pad=(14, 6),
    colors=dict(bg="#f4f6fa", panel="#ffffff", ink="#1b2230", muted="#5b6678", field="#ffffff",
                border="#d5dbe6", select_bg="#eaf0fd", select_fg="#1b2230",
                button="#ffffff", button_fg="#1b2230", button_active="#eef1f6",
                primary="#2457d6", primary_fg="#ffffff", primary_active="#1a3f9e",
                secondary="#ffffff", secondary_fg="#1b2230", secondary_active="#eef1f6",
                danger="#ffffff", danger_fg="#b42318", danger_active="#fdecea",
                progress="#2457d6", trough="#e3e7ef", local="#5b6678", remote="#5b6678",
                title="#1b2230", accent2="#2457d6", ok="#1f9d55", heading="#fafbfd"),
    labels={"upload": "↑ Upload", "download": "↓ Download", "apply_to": "Actions apply to:",
            "status_on": "● Connected", "status_off": "○ Not connected"},
))

_add(Look(
    key="midnight", name="Midnight (dark)",
    fonts=("JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New"),
    size=10, title_size=15, actions="bottom", row_height=26, pad=(10, 5), big_pad=(12, 5),
    log_lines=8,
    colors=dict(bg="#0f1419", panel="#131a21", ink="#e6edf3", muted="#8b98a8", field="#161d24",
                border="#2a3541", select_bg="#1d3a3a", select_fg="#e6edf3",
                button="#161d24", button_fg="#e6edf3", button_active="#222b35",
                primary="#5eead4", primary_fg="#0f1419", primary_active="#99f6e4",
                secondary="#161d24", secondary_fg="#e6edf3", secondary_active="#222b35",
                danger="#161d24", danger_fg="#ff8a8a", danger_active="#2a1a1d",
                progress="#5eead4", trough="#0b0f13", local="#7dd3fc", remote="#fbbf24",
                title="#ffffff", accent2="#5eead4", ok="#5eead4", heading="#131a21",
                log_bg="#0b0f13"),
    labels={"host": "host", "port": "port", "user": "user", "password": "pass", "tls": "tls",
            "connect": "connect", "disconnect": "disconnect", "local": "LOCAL", "remote": "REMOTE",
            "upload": "upload →", "download": "← download", "new_folder": "mkdir",
            "rename": "rename", "delete": "delete", "refresh": "refresh", "apply_to": "acts on:",
            "status_on": "● connected", "status_off": "○ offline",
            "hint": "F5 upload · F6 download"},
))

_add(Look(
    key="big", name="Big friendly buttons",
    fonts=("Nunito", "Segoe UI", "Verdana", "DejaVu Sans"),
    size=13, title_size=20, actions="bottom", row_height=40, pad=(14, 8), big_pad=(24, 14),
    log_lines=3, toolbar_rows=2,
    colors=dict(bg="#fff7ef", panel="#ffffff", ink="#2b2118", muted="#6e5d4e", field="#fbf6f0",
                border="#e4d4c3", select_bg="#e3f4ef", select_fg="#2b2118",
                button="#ffffff", button_fg="#2b2118", button_active="#f6ece2",
                primary="#0b6b5c", primary_fg="#ffffff", primary_active="#084d42",
                secondary="#d9622b", secondary_fg="#ffffff", secondary_active="#b54d1f",
                danger="#ffffff", danger_fg="#a3261a", danger_active="#fbe9e6",
                progress="#0b6b5c", trough="#f1e6da", local="#2b2118", remote="#2b2118",
                title="#2b2118", accent2="#0b6b5c", ok="#0b5d3f", heading="#fbf6f0"),
    labels={"host": "Server", "user": "Name", "tls": "Safe (TLS)",
            "connect": "Connect", "disconnect": "Disconnect",
            "local": "My computer", "remote": "The server",
            "upload": "Put on server →", "download": "← Bring to my computer",
            "apply_to": "Buttons work on:",
            "status_on": "✓ Connected to", "status_off": "Not connected"},
))

_add(Look(key="classic", name="Classic (plain)", header=False))

DEFAULT_LOOK = "crayon"


def get_look(key: Optional[str]) -> Look:
    return LOOKS.get(key or "", LOOKS[DEFAULT_LOOK])


def pick_family(root: tk.Misc, candidates: Sequence[str]) -> Optional[str]:
    installed = {f.lower(): f for f in tkfont.families(root)}
    for name in candidates:
        if name.lower() in installed:
            return installed[name.lower()]
    return None


class Fonts:
    """The fonts a look resolved to on this computer."""

    def __init__(self, root: tk.Misc, look: Look) -> None:
        family = pick_family(root, look.fonts) or tkfont.nametofont("TkDefaultFont", root).actual("family")
        self.family = family
        self.body = (family, look.size)
        self.bold = (family, look.size, "bold")
        self.big = (family, look.size + 2, "bold")
        self.pane_title = (family, look.size + (6 if look.sketchy else 1), "bold")
        self.title = (family, look.title_size, "bold")
        self.small = (family, max(look.size - 1, 8))


def apply_look(root: tk.Misc, look: Look, system_theme: str) -> Fonts:
    """Switch ttk to this look's theme (creating it the first time) and return its fonts."""
    style = ttk.Style(root)
    fonts = Fonts(root, look)
    c = look.colors
    if c is None:
        style.theme_use(system_theme)
        style.configure("LocalTitle.TLabel", font=fonts.bold)
        style.configure("RemoteTitle.TLabel", font=fonts.bold)
        return fonts

    name = "msftp-" + look.key
    if name not in style.theme_names():
        style.theme_create(name, parent="clam")
    style.theme_use(name)
    thick = 2 if look.sketchy else 1

    style.configure(".", background=c["bg"], foreground=c["ink"], font=fonts.body,
                    bordercolor=c["border"], lightcolor=c["bg"], darkcolor=c["bg"],
                    troughcolor=c["trough"], fieldbackground=c["field"], focuscolor=c["accent2"],
                    selectbackground=c["select_bg"], selectforeground=c["select_fg"],
                    insertcolor=c["ink"], arrowcolor=c["ink"])
    style.configure("TFrame", background=c["bg"])
    style.configure("Card.TFrame", background=c["panel"])
    style.configure("TLabel", background=c["bg"], foreground=c["ink"])
    style.configure("Card.TLabel", background=c["panel"], foreground=c["muted"], font=fonts.small)
    style.configure("Muted.TLabel", foreground=c["muted"], font=fonts.small)
    style.configure("Header.TLabel", font=fonts.title, foreground=c["title"])
    style.configure("Status.TLabel", font=fonts.bold, foreground=c["ok"])
    style.configure("LocalTitle.TLabel", background=c["panel"], foreground=c["local"], font=fonts.pane_title)
    style.configure("RemoteTitle.TLabel", background=c["panel"], foreground=c["remote"], font=fonts.pane_title)
    style.configure("TEntry", fieldbackground=c["field"], foreground=c["ink"], bordercolor=c["border"],
                    lightcolor=c["field"], darkcolor=c["field"], padding=4)
    style.map("TEntry", bordercolor=[("focus", c["accent2"])], lightcolor=[("focus", c["field"])])
    for kind in ("TCheckbutton", "TRadiobutton"):
        style.configure(kind, background=c["bg"], foreground=c["ink"], indicatorbackground=c["field"],
                        indicatorforeground=c["ink"], upperbordercolor=c["border"],
                        lowerbordercolor=c["border"])
        style.map(kind, background=[("active", c["bg"])],
                  indicatorbackground=[("selected", c["primary"]), ("active", c["field"])],
                  indicatorforeground=[("selected", c["primary_fg"])])

    def button(style_name: str, bg: str, fg: str, active: str, pad: Tuple[int, int], fnt) -> None:
        style.configure(style_name, background=bg, foreground=fg, bordercolor=c["border"],
                        lightcolor=bg, darkcolor=bg, borderwidth=thick, relief="solid",
                        padding=pad, font=fnt, anchor="center")
        style.map(style_name, background=[("pressed", active), ("active", active)],
                  lightcolor=[("pressed", active), ("active", active)],
                  darkcolor=[("pressed", active), ("active", active)],
                  bordercolor=[("focus", c["accent2"])])

    button("TButton", c["button"], c["button_fg"], c["button_active"], look.pad, fonts.body)
    button("Primary.TButton", c["primary"], c["primary_fg"], c["primary_active"], look.big_pad, fonts.big)
    button("Secondary.TButton", c["secondary"], c["secondary_fg"], c["secondary_active"], look.big_pad,
           fonts.big)
    button("Danger.TButton", c["danger"], c["danger_fg"], c["danger_active"], look.pad, fonts.body)

    row = look.row_height or fonts_linespace(root, fonts.body) + 8
    style.configure("Treeview", background=c["field"], fieldbackground=c["field"], foreground=c["ink"],
                    bordercolor=c["border"], lightcolor=c["field"], darkcolor=c["field"],
                    rowheight=row, font=fonts.body)
    style.map("Treeview", background=[("selected", c["select_bg"])],
              foreground=[("selected", c["select_fg"])])
    style.configure("Treeview.Heading", background=c["heading"], foreground=c["muted"], font=fonts.bold,
                    bordercolor=c["border"], lightcolor=c["heading"], darkcolor=c["heading"],
                    relief="flat", padding=(6, 4))
    style.map("Treeview.Heading", background=[("active", c["heading"])])
    style.configure("Horizontal.TProgressbar", background=c["progress"], troughcolor=c["trough"],
                    bordercolor=c["border"], lightcolor=c["progress"], darkcolor=c["progress"],
                    thickness=18 if look.sketchy else 10)
    style.configure("Vertical.TScrollbar", background=c["button"], troughcolor=c["field"],
                    bordercolor=c["border"], lightcolor=c["button"], darkcolor=c["button"],
                    arrowcolor=c["muted"])
    style.configure("TPanedwindow", background=c["bg"])
    style.configure("TSeparator", background=c["border"])
    return fonts


def fonts_linespace(root: tk.Misc, spec) -> int:
    return tkfont.Font(root=root, font=spec).metrics("linespace")
