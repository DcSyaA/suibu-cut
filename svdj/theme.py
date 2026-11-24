"""Visual language for the application."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk


# Core palette
BG = "#0C0F15"
BG_GLOW = "#10141D"
SURFACE = "#151A23"
SURFACE_ALT = "#191F2A"
SURFACE_HOVER = "#232B39"
BORDER = "#252D3B"
BORDER_SOFT = "#1E2531"
INPUT = "#0F131A"
TEXT = "#F3F6FC"
TEXT_SOFT = "#C8D0DE"
MUTED = "#7E899D"
MUTED_DARK = "#596475"
ACCENT = "#8B6CFF"
ACCENT_HOVER = "#9D82FF"
ACCENT_PRESS = "#7657E7"
ACCENT_DEEP = "#3B2C72"
ACCENT_SOFT = "#26203F"
MINT = "#43D7A3"
MINT_DEEP = "#16382E"
AUDITION = "#6CC7FF"
AUDITION_DEEP = "#18354A"
ORANGE = "#FFB35F"
ORANGE_DEEP = "#3D2A15"
RED = "#FF6B7A"
RED_DEEP = "#3D1B23"
WHITE = "#FFFFFF"


class Fonts:
    ui = "Microsoft YaHei UI"
    display = "Microsoft YaHei UI"
    mono = "Consolas"

    @classmethod
    def load(cls, root: tk.Misc) -> None:
        families = set(tkfont.families(root))
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Segoe UI"):
            if name in families:
                cls.ui = name
                cls.display = name
                break
        for name in ("Cascadia Mono", "Consolas", "Courier New"):
            if name in families:
                cls.mono = name
                break

    @classmethod
    def font(cls, size: int = 10, weight: str = "normal", family: str | None = None):
        return (family or cls.ui, size, weight)


def setup_styles(root: tk.Misc) -> ttk.Style:
    Fonts.load(root)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(
        "App.Treeview",
        background=SURFACE,
        fieldbackground=SURFACE,
        foreground=TEXT_SOFT,
        borderwidth=0,
        relief="flat",
        rowheight=42,
        font=Fonts.font(10),
    )
    style.configure(
        "App.Treeview.Heading",
        background=SURFACE,
        foreground=MUTED,
        borderwidth=0,
        relief="flat",
        padding=(8, 9),
        font=Fonts.font(9, "bold"),
    )
    style.map(
        "App.Treeview",
        background=[("selected", "#2A2247")],
        foreground=[("selected", WHITE)],
    )
    style.map(
        "App.Treeview.Heading",
        background=[("active", SURFACE_ALT)],
        foreground=[("active", TEXT_SOFT)],
    )
    style.layout("App.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    style.configure(
        "Vertical.TScrollbar",
        gripcount=0,
        background="#2A3241",
        darkcolor=SURFACE,
        lightcolor=SURFACE,
        troughcolor=SURFACE,
        bordercolor=SURFACE,
        arrowcolor=MUTED,
        relief="flat",
        width=9,
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("active", "#3A4457")],
        arrowcolor=[("active", TEXT_SOFT)],
    )
    style.configure(
        "Horizontal.TScrollbar",
        gripcount=0,
        background="#2A3241",
        darkcolor=SURFACE,
        lightcolor=SURFACE,
        troughcolor=SURFACE,
        bordercolor=SURFACE,
        arrowcolor=MUTED,
        relief="flat",
        width=9,
    )
    style.map(
        "Horizontal.TScrollbar",
        background=[("active", "#3A4457")],
        arrowcolor=[("active", TEXT_SOFT)],
    )

    style.configure(
        "App.TCombobox",
        fieldbackground=INPUT,
        background=SURFACE_ALT,
        foreground=TEXT,
        arrowcolor=MUTED,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        insertcolor=TEXT,
        relief="flat",
        padding=(10, 6),
        font=Fonts.font(10),
    )
    style.map(
        "App.TCombobox",
        fieldbackground=[("readonly", INPUT), ("disabled", INPUT)],
        foreground=[("readonly", TEXT), ("disabled", MUTED)],
        background=[("active", SURFACE_HOVER)],
        bordercolor=[("focus", ACCENT), ("active", BORDER)],
        arrowcolor=[("active", TEXT_SOFT)],
    )
    root.option_add("*TCombobox*Listbox.background", SURFACE_ALT)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DEEP)
    root.option_add("*TCombobox*Listbox.selectForeground", WHITE)
    root.option_add("*TCombobox*Listbox.font", Fonts.font(10))

    style.configure(
        "App.TCheckbutton",
        background=SURFACE,
        foreground=TEXT_SOFT,
        focuscolor=SURFACE,
        font=Fonts.font(9),
        indicatorbackground=INPUT,
        indicatorforeground=ACCENT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=0,
    )
    style.map(
        "App.TCheckbutton",
        background=[("active", SURFACE)],
        foreground=[("active", TEXT)],
        indicatorbackground=[("selected", ACCENT), ("active", SURFACE_HOVER)],
        indicatorforeground=[("selected", WHITE)],
        bordercolor=[("focus", ACCENT), ("active", BORDER)],
    )

    style.configure(
        "App.Horizontal.TScale",
        background=SURFACE,
        troughcolor=INPUT,
        bordercolor=SURFACE,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        sliderrelief="flat",
        sliderlength=15,
    )
    style.map("App.Horizontal.TScale", background=[("active", SURFACE)])

    style.configure("App.TSeparator", background=BORDER_SOFT)
    return style
