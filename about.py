"""The About window and the app icon.

Same credit as the other Hamilton Group Programming apps: "Built by", the HGP logo,
"with the help of Claude".
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import ttk
from typing import Optional

from ftpclient import __version__
from looks import Look

APP_NAME = "More Simple FTP"
TAGLINE = "Move files between your computer and a server, simply."
CREDIT = "Built by Hamilton Group Programming with the help of Claude"
REVERSED_LOGO_BG = "#3c3b37"   # the reversed logo is drawn on this colour


def asset_path(name: str) -> str:
    """Path to a file in assets/, from source or from inside the PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", name)


def _load(master: tk.Misc, name: str, shrink: int = 1) -> Optional[tk.PhotoImage]:
    try:
        img = tk.PhotoImage(master=master, file=asset_path(name))
    except (tk.TclError, OSError):
        return None
    return img.subsample(shrink) if shrink > 1 else img


def set_window_icon(root: tk.Tk) -> None:
    """Use the crayon icon for the window and taskbar (Windows also gets it from the exe)."""
    ico = asset_path("icon.ico")
    if sys.platform == "win32" and os.path.exists(ico):
        try:
            root.iconbitmap(default=ico)
            return
        except tk.TclError:
            pass
    img = _load(root, "icon.png")
    if img is not None:
        root.iconphoto(True, img)
        root._app_icon = img  # keep a reference or Tk drops the image


def is_dark(look: Look) -> bool:
    bg = (look.colors or {}).get("bg", "#ffffff").lstrip("#")
    r, g, b = (int(bg[i:i + 2], 16) for i in (0, 2, 4))
    return (r * 299 + g * 587 + b * 114) / 1000 < 128


def show_about(app: tk.Tk, look: Look, fonts, underline=None) -> tk.Toplevel:
    """Open the About window over ``app``, dressed in the current look.

    ``underline(master, colour, bg, width)`` draws the crayon squiggle under the name.
    """
    c = look.colors or {}
    bg = c.get("bg") or ttk.Style(app).lookup("TFrame", "background") or None
    win = tk.Toplevel(app)
    win.title(f"About {APP_NAME}")
    win.resizable(False, False)
    win.transient(app)
    if bg:
        win.configure(bg=bg)

    body = ttk.Frame(win, padding=(32, 24))
    body.pack(fill="both", expand=True)

    icon = _load(win, "icon.png", shrink=4)          # 512 px -> 128 px
    if icon is not None:
        ttk.Label(body, image=icon).pack()
    ttk.Label(body, text=APP_NAME, font=fonts.title,
              foreground=c.get("title")).pack(pady=(8, 0))
    if look.sketchy and underline is not None:
        underline(body, c["accent2"], c["bg"], width=200).pack()
    ttk.Label(body, text=f"Version {__version__}", font=fonts.body).pack(pady=(6, 0))
    ttk.Label(body, text=TAGLINE, font=fonts.small,
              foreground=c.get("muted")).pack(pady=(2, 18))

    credit = ttk.Frame(body)
    credit.pack()
    dark = is_dark(look) if look.colors else False
    ttk.Label(credit, text="Built by", font=fonts.small, foreground=c.get("muted")).pack()
    logo = _load(win, "hgp_logo_reversed.png" if dark else "hgp_logo.png", shrink=2)  # 450 px wide
    if logo is not None:
        holder = tk.Frame(credit, bg=REVERSED_LOGO_BG if dark else bg,
                          padx=10 if dark else 0, pady=8 if dark else 0)
        holder.pack(pady=4)
        tk.Label(holder, image=logo, bd=0, bg=REVERSED_LOGO_BG if dark else bg).pack()
    else:
        ttk.Label(credit, text="Hamilton Group Programming", font=fonts.bold).pack(pady=4)
    ttk.Label(credit, text="with the help of Claude", font=fonts.small,
              foreground=c.get("muted")).pack()

    ttk.Button(body, text="Close", command=win.destroy,
               style="Primary.TButton" if look.colors else "TButton").pack(pady=(20, 0))
    win.bind("<Escape>", lambda e: win.destroy())
    win._images = (icon, logo)  # keep references alive while the window is open

    # centre over the main window
    win.update_idletasks()
    x = app.winfo_rootx() + (app.winfo_width() - win.winfo_width()) // 2
    y = app.winfo_rooty() + (app.winfo_height() - win.winfo_height()) // 3
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    win.grab_set()
    win.focus_set()
    return win
