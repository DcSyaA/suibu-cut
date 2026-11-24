"""Main window for 随舞剪辑台."""

from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from collections import OrderedDict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from . import theme
from .audio import (
    AUDIO_EXTENSIONS,
    AudioCancelled,
    AudioError,
    ClipSpec,
    ExportPlan,
    TransitionSpec,
    build_export_plan,
    format_duration,
    format_duration_zh,
    load_waveform,
    make_unique_path,
    parse_time,
    probe_audio,
    render_preview,
    run_export,
)
from .history import HistoryManager
from .playback import PreviewPlayer
from .project import (
    APP_NAME,
    OUTPUT_FOLDER,
    Project,
    ProjectError,
    Track,
)
from .widgets import (
    Card,
    ProgressBar,
    RoundedButton,
    TimeEntry,
    Tooltip,
    WaveformView,
    rounded_rectangle,
)


FORMAT_CHOICES = {
    "MP3": "mp3",
    "WAV": "wav",
    "M4A (AAC)": "m4a",
    "FLAC": "flac",
}
FORMAT_LABELS = {value: key for key, value in FORMAT_CHOICES.items()}
BITRATE_CHOICES = ("320k", "256k", "192k")
TEMP_PREFIX = "suibu_preview_"


def _safe_float(text: str, fallback: float) -> float:
    try:
        value = float(str(text).strip().replace(",", "."))
        return value
    except (TypeError, ValueError):
        return fallback


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(". ")
    return name or "随舞混剪"


def _short_time(seconds: float) -> str:
    return format_duration(seconds, precise=False)


class SuibuApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__(className="SuibuCut")
        self.withdraw()
        self.title(f"{APP_NAME} · 自动拼剪随舞歌单")
        self.configure(bg=theme.BG)
        theme.setup_styles(self)
        self.minsize(1160, 760)
        self._apply_window_geometry()
        self._set_icon()

        self.project: Project | None = None
        self.current_track: Track | None = None
        self.events: queue.Queue = queue.Queue()
        self.player = PreviewPlayer(
            self,
            on_tick=self._on_playback_tick,
            on_finished=self._on_playback_finished,
        )
        self._events_after_id: str | None = None
        self._save_after_id: str | None = None
        self._hint_after_id: str | None = None
        self._loading_ui = True
        self._refreshing_tree = False
        self._closing = False
        self._wave_token = 0
        self._preview_token = 0
        self._preview_kind: str | None = None
        self._preview_preparing = False
        self._preview_start = 0.0
        self._preview_duration = 0.0
        self._wave_cache: OrderedDict[tuple, object] = OrderedDict()
        self._probing = False
        self._probe_attempted: set[str] = set()
        self._export_running = False
        self._export_cancel: threading.Event | None = None
        self._export_path: Path | None = None
        self._last_export: Path | None = None
        self._sort_key: str | None = None
        self._sort_reverse = False
        self._drag_iid: str | None = None
        self._drag_start_y = 0
        self._drag_started = False
        self._temp_dir = self._create_temp_dir()
        self._settings_file = self._settings_path()
        self._settings = self._load_settings()
        self.history = HistoryManager(limit=100)
        self._restoring_history = False
        self._wave_dragging = False

        self._build_ui()
        self._bind_shortcuts()
        self._refresh_all()
        self._reset_history()
        self._events_after_id = self.after(50, self._poll_events)
        self._loading_ui = False
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(40, self.deiconify)
        self.after(90, self._apply_dark_titlebar)
        self.after(120, self._startup)

    # ------------------------------------------------------------------
    # Window / resources

    def _apply_window_geometry(self) -> None:
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(1340, max(1160, screen_w - 140))
        height = min(870, max(760, screen_h - 140))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _set_icon(self) -> None:
        icon_path = Path(__file__).resolve().parent.parent / "assets" / "icon.png"
        if icon_path.is_file():
            try:
                self._icon_image = tk.PhotoImage(file=str(icon_path))
                self.iconphoto(True, self._icon_image)
            except tk.TclError:
                pass

    def _apply_dark_titlebar(self) -> None:
        """Ask DWM for a dark native caption so the window frame matches the UI."""
        if os.name != "nt":
            return
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            if not hwnd:
                hwnd = self.winfo_id()
            dwm = ctypes.windll.dwmapi
            value = ctypes.c_int(1)
            result = dwm.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)
            )
            if result != 0:
                dwm.DwmSetWindowAttribute(
                    hwnd, 19, ctypes.byref(value), ctypes.sizeof(value)
                )
            for attribute, color in (
                (35, 0x00150F0C),  # caption  #0C0F15
                (36, 0x00FCF6F3),  # text     #F3F6FC
                (34, 0x003B2D25),  # border   #252D3B
            ):
                dwm.DwmSetWindowAttribute(
                    hwnd,
                    attribute,
                    ctypes.byref(ctypes.c_int(color)),
                    ctypes.sizeof(ctypes.c_int),
                )
        except Exception:
            pass

    def _create_temp_dir(self) -> Path:
        candidates = [
            Path(tempfile.gettempdir()) / f"{TEMP_PREFIX}{os.getpid()}",
            Path.cwd() / ".preview_cache",
        ]
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                test = candidate / ".write_test"
                test.write_text("ok", encoding="utf-8")
                test.unlink(missing_ok=True)
                return candidate
            except OSError:
                continue
        return Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))

    def _settings_path(self) -> Path:
        base = os.getenv("APPDATA") or str(Path.home())
        return Path(base) / "SuiWuMixer" / "settings.json"

    def _load_settings(self) -> dict:
        try:
            return json.loads(self._settings_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_settings(self) -> None:
        data = dict(self._settings)
        if self.project:
            data["last_project"] = str(self.project.folder)
        try:
            self._settings_file.parent.mkdir(parents=True, exist_ok=True)
            self._settings_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ------------------------------------------------------------------
    # UI construction

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self.workspace = tk.Frame(self, bg=theme.BG)
        self.workspace.grid(row=1, column=0, sticky="nsew", padx=22, pady=(2, 0))
        self.workspace.grid_rowconfigure(0, weight=1)
        self.workspace.grid_columnconfigure(0, minsize=430)
        self.workspace.grid_columnconfigure(1, weight=1)

        self._build_playlist(self.workspace)
        self._build_editor(self.workspace)
        self._build_footer()

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=theme.BG, height=76)
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(10, 6))
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        logo = tk.Canvas(header, width=46, height=46, bg=theme.BG, highlightthickness=0, bd=0)
        logo.grid(row=0, column=0, rowspan=2, sticky="w")
        rounded_rectangle(logo, 3, 3, 43, 43, 12, fill=theme.ACCENT, outline="")
        heights = (8, 14, 21, 12, 9)
        start_x = 11
        for index, bar_height in enumerate(heights):
            x = start_x + index * 5.2
            color = theme.MINT if index == 2 else theme.WHITE
            logo.create_line(
                x,
                23 - bar_height / 2,
                x,
                23 + bar_height / 2,
                fill=color,
                width=3.2,
                capstyle=tk.ROUND,
            )

        brand = tk.Frame(header, bg=theme.BG)
        brand.grid(row=0, column=1, sticky="w", padx=(12, 0))
        tk.Label(
            brand,
            text=APP_NAME,
            bg=theme.BG,
            fg=theme.TEXT,
            font=theme.Fonts.font(17, "bold"),
        ).pack(anchor="w")
        tk.Label(
            brand,
            text="SUIBU CUT  /  把歌单剪成一场随舞",
            bg=theme.BG,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).pack(anchor="w", pady=(2, 0))

        self.project_chip = tk.Frame(
            header,
            bg=theme.SURFACE,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            bd=0,
        )
        self.project_chip.grid(row=0, column=2, rowspan=2, sticky="e", padx=(12, 10))
        chip_inner = tk.Frame(self.project_chip, bg=theme.SURFACE)
        chip_inner.pack(padx=12, pady=7)
        chip_top = tk.Frame(chip_inner, bg=theme.SURFACE)
        chip_top.pack(fill="x")
        self.project_dot = tk.Canvas(
            chip_top,
            width=8,
            height=8,
            bg=theme.SURFACE,
            highlightthickness=0,
            bd=0,
        )
        self.project_dot.pack(side="left", padx=(0, 6))
        self.project_dot.create_oval(1, 1, 7, 7, fill=theme.MUTED_DARK, outline="")
        tk.Label(
            chip_top,
            text="当前项目",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).pack(side="left")
        self.project_name_label = tk.Label(
            chip_top,
            text="未打开项目",
            bg=theme.SURFACE,
            fg=theme.TEXT_SOFT,
            font=theme.Fonts.font(10, "bold"),
            anchor="w",
            width=25,
        )
        self.project_name_label.pack(side="left", padx=(9, 0))
        self.project_detail_label = tk.Label(
            chip_inner,
            text="选择一个歌单文件夹开始",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
            anchor="w",
            width=41,
        )
        self.project_detail_label.pack(anchor="w", pady=(1, 0))
        self.project_tooltip = Tooltip(self.project_chip, "尚未打开项目")
        self.project_chip.bind("<Double-Button-1>", lambda _e: self._reveal_project_folder())

        self.open_btn = RoundedButton(
            header,
            text="打开文件夹",
            command=self._open_folder_dialog,
            style="primary",
            width=124,
            height=40,
            font_size=9,
            background=theme.BG,
        )
        self.open_btn.grid(row=0, column=3, rowspan=2, sticky="e")

    def _build_playlist(self, parent: tk.Misc) -> None:
        card = Card(parent, padding=16)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        body = card.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(3, weight=1)

        header = tk.Frame(body, bg=theme.SURFACE)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(
            header,
            text="曲目",
            bg=theme.SURFACE,
            fg=theme.TEXT,
            font=theme.Fonts.font(13, "bold"),
        ).pack(side="left")
        self.count_badge = tk.Label(
            header,
            text="0 首",
            bg=theme.ACCENT_SOFT,
            fg="#C3B3FF",
            font=theme.Fonts.font(8, "bold"),
            padx=8,
            pady=3,
        )
        self.count_badge.pack(side="left", padx=(8, 0))
        self.add_btn = RoundedButton(
            header,
            text="＋ 添加",
            command=self._add_files,
            style="ghost",
            width=72,
            height=30,
            font_size=8,
            background=theme.SURFACE,
        )
        self.add_btn.pack(side="right")
        self.redo_btn = RoundedButton(
            header,
            text="↷",
            command=self._redo,
            style="quiet",
            width=30,
            height=30,
            font_size=12,
            background=theme.SURFACE,
            padx=4,
        )
        self.redo_btn.pack(side="right", padx=(0, 4))
        self.undo_btn = RoundedButton(
            header,
            text="↶",
            command=self._undo,
            style="quiet",
            width=30,
            height=30,
            font_size=12,
            background=theme.SURFACE,
            padx=4,
        )
        self.undo_btn.pack(side="right", padx=(0, 4))
        self._undo_tooltip = Tooltip(self.undo_btn, "撤销 (Ctrl+Z)")
        self._redo_tooltip = Tooltip(self.redo_btn, "重做 (Ctrl+Y)")

        toolbar = tk.Frame(body, bg=theme.SURFACE)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(9, 8))
        tk.Label(
            toolbar,
            text="排序",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).pack(side="left", padx=(0, 7))
        self.name_sort_btn = RoundedButton(
            toolbar,
            text="名称",
            command=lambda: self._sort_tracks("name"),
            style="quiet",
            width=58,
            height=26,
            font_size=8,
            bold=False,
            background=theme.SURFACE,
            padx=8,
        )
        self.name_sort_btn.pack(side="left", padx=(0, 4))
        self.duration_sort_btn = RoundedButton(
            toolbar,
            text="时长",
            command=lambda: self._sort_tracks("duration"),
            style="quiet",
            width=52,
            height=26,
            font_size=8,
            bold=False,
            background=theme.SURFACE,
            padx=8,
        )
        self.duration_sort_btn.pack(side="left", padx=(0, 4))
        self.clip_sort_btn = RoundedButton(
            toolbar,
            text="选段",
            command=lambda: self._sort_tracks("clip"),
            style="quiet",
            width=52,
            height=26,
            font_size=8,
            bold=False,
            background=theme.SURFACE,
            padx=8,
        )
        self.clip_sort_btn.pack(side="left")
        tk.Label(
            toolbar,
            text="拖拽可排序",
            bg=theme.SURFACE,
            fg=theme.MUTED_DARK,
            font=theme.Fonts.font(8),
        ).pack(side="right")

        tree_frame = tk.Frame(body, bg=theme.SURFACE)
        tree_frame.grid(row=3, column=0, sticky="nsew")
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        columns = ("index", "name", "duration", "clip", "enabled")
        self.tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            style="App.Treeview",
            selectmode="browse",
        )
        headings = {
            "index": "#",
            "name": "歌曲",
            "duration": "时长",
            "clip": "剪辑段",
            "enabled": "使用",
        }
        for column, label in headings.items():
            self.tree.heading(
                column,
                text=label,
                command=(
                    lambda key=column: self._sort_tracks(
                        {"name": "name", "duration": "duration", "clip": "clip"}.get(key, "name")
                    )
                    if key in {"name", "duration", "clip"}
                    else None
                ),
            )
        self.tree.column("index", width=29, minwidth=29, stretch=False, anchor="center")
        self.tree.column("name", width=178, minwidth=110, stretch=True, anchor="w")
        self.tree.column("duration", width=47, minwidth=43, stretch=False, anchor="e")
        self.tree.column("clip", width=90, minwidth=82, stretch=False, anchor="center")
        self.tree.column("enabled", width=36, minwidth=33, stretch=False, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self.tree.yview,
            style="Vertical.TScrollbar",
        )
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(3, 0))
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.tag_configure("normal", foreground=theme.TEXT_SOFT)
        self.tree.tag_configure("muted", foreground=theme.MUTED_DARK)
        self.tree.tag_configure("missing", foreground=theme.RED)
        self.tree.tag_configure("error", foreground=theme.ORANGE)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<ButtonPress-1>", self._on_tree_press, add="+")
        self.tree.bind("<B1-Motion>", self._on_tree_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._on_tree_release, add="+")
        self.tree.bind("<Button-3>", self._on_tree_context, add="+")
        self.tree.bind("<Double-Button-1>", self._on_tree_double_click, add="+")

        self.empty_hint = tk.Label(
            tree_frame,
            text="还没有歌曲\n打开一个歌单文件夹开始",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(9),
            justify="center",
            cursor="hand2",
        )
        self.empty_hint.place(relx=0.5, rely=0.44, anchor="center")
        self.empty_hint.bind("<Button-1>", self._on_empty_hint_click)

        actions = tk.Frame(body, bg=theme.SURFACE)
        actions.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.up_btn = RoundedButton(
            actions,
            text="↑ 上移",
            command=lambda: self._move_selected(-1),
            style="ghost",
            width=67,
            height=31,
            font_size=8,
            bold=False,
            background=theme.SURFACE,
            padx=8,
        )
        self.up_btn.pack(side="left", padx=(0, 4))
        self.down_btn = RoundedButton(
            actions,
            text="↓ 下移",
            command=lambda: self._move_selected(1),
            style="ghost",
            width=67,
            height=31,
            font_size=8,
            bold=False,
            background=theme.SURFACE,
            padx=8,
        )
        self.down_btn.pack(side="left", padx=(0, 4))
        self.toggle_btn = RoundedButton(
            actions,
            text="停用",
            command=self._toggle_selected,
            style="ghost",
            width=58,
            height=31,
            font_size=8,
            bold=False,
            background=theme.SURFACE,
            padx=7,
        )
        self.toggle_btn.pack(side="left", padx=(0, 4))
        self.remove_btn = RoundedButton(
            actions,
            text="移除",
            command=self._remove_selected,
            style="danger",
            width=54,
            height=31,
            font_size=8,
            bold=False,
            background=theme.SURFACE,
            padx=7,
        )
        self.remove_btn.pack(side="right")

        tk.Label(
            body,
            text="点击「使用」列快速启用 / 停用 · 双击歌曲也可切换",
            bg=theme.SURFACE,
            fg=theme.MUTED_DARK,
            font=theme.Fonts.font(8),
            anchor="center",
        ).grid(row=5, column=0, sticky="ew", pady=(7, 0))

    def _build_editor(self, parent: tk.Misc) -> None:
        right = tk.Frame(parent, bg=theme.BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1, minsize=300)
        right.grid_rowconfigure(1, minsize=220)

        wave_card = Card(right, padding=16)
        wave_card.grid(row=0, column=0, sticky="nsew")
        body = wave_card.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        top = tk.Frame(body, bg=theme.SURFACE)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        text_block = tk.Frame(top, bg=theme.SURFACE)
        text_block.grid(row=0, column=0, sticky="ew")
        self.track_title_label = tk.Label(
            text_block,
            text="选择一首歌开始",
            bg=theme.SURFACE,
            fg=theme.TEXT,
            font=theme.Fonts.font(14, "bold"),
            anchor="w",
        )
        self.track_title_label.pack(fill="x")
        self.track_meta_label = tk.Label(
            text_block,
            text="在左侧歌单中选择歌曲，然后拖出要剪进混剪的片段",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(9),
            anchor="w",
        )
        self.track_meta_label.pack(fill="x", pady=(3, 0))
        zoom_group = tk.Frame(top, bg=theme.SURFACE)
        zoom_group.grid(row=0, column=1, sticky="ne", padx=(10, 0))
        self.zoom_out_btn = RoundedButton(
            zoom_group,
            text="−",
            command=lambda: self._zoom_wave(1 / 1.5),
            style="quiet",
            width=28,
            height=26,
            font_size=11,
            background=theme.SURFACE,
            padx=4,
        )
        self.zoom_out_btn.pack(side="left", padx=(0, 3))
        self.zoom_in_btn = RoundedButton(
            zoom_group,
            text="+",
            command=lambda: self._zoom_wave(1.5),
            style="quiet",
            width=28,
            height=26,
            font_size=10,
            background=theme.SURFACE,
            padx=4,
        )
        self.zoom_in_btn.pack(side="left", padx=(0, 3))
        self.fit_wave_btn = RoundedButton(
            zoom_group,
            text="全览",
            command=self._fit_wave,
            style="quiet",
            width=44,
            height=26,
            font_size=8,
            bold=False,
            background=theme.SURFACE,
            padx=5,
        )
        self.fit_wave_btn.pack(side="left")
        Tooltip(self.zoom_out_btn, "缩小波形（Ctrl + 滚轮）")
        Tooltip(self.zoom_in_btn, "放大波形（Ctrl + 滚轮）")
        Tooltip(self.fit_wave_btn, "显示整首歌的波形")
        self.track_state_badge = tk.Label(
            top,
            text="未选择",
            bg=theme.ACCENT_SOFT,
            fg="#C3B3FF",
            font=theme.Fonts.font(8, "bold"),
            padx=10,
            pady=4,
        )
        self.track_state_badge.grid(row=0, column=2, sticky="ne", padx=(8, 0))

        wave_area = tk.Frame(body, bg=theme.SURFACE)
        wave_area.grid(row=1, column=0, sticky="nsew", pady=(12, 10))
        wave_area.grid_columnconfigure(0, weight=1)
        wave_area.grid_rowconfigure(0, weight=1)
        self.wave = WaveformView(
            wave_area,
            on_change=self._on_wave_change,
            on_view_change=self._on_wave_view_change,
            on_audition=self._on_wave_audition,
            on_context=self._on_wave_context,
        )
        self.wave.grid(row=0, column=0, sticky="nsew")
        self.wave.configure(height=190)
        self.wave_hscroll = ttk.Scrollbar(
            wave_area,
            orient="horizontal",
            command=self._on_wave_scrollbar,
            style="Horizontal.TScrollbar",
        )
        self.wave_hscroll.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        controls = tk.Frame(body, bg=theme.SURFACE)
        controls.grid(row=2, column=0, sticky="ew")
        controls.grid_columnconfigure(6, weight=1)

        start_block = tk.Frame(controls, bg=theme.SURFACE)
        start_block.grid(row=0, column=0, sticky="w")
        start_label_row = tk.Frame(start_block, bg=theme.SURFACE)
        start_label_row.pack(anchor="w")
        tk.Canvas(
            start_label_row,
            width=7,
            height=7,
            bg=theme.SURFACE,
            highlightthickness=0,
            bd=0,
        ).pack(side="left", padx=(0, 5))
        self._start_dot = start_label_row.winfo_children()[0]
        self._start_dot.create_oval(1, 1, 6, 6, fill=theme.MINT, outline="")
        tk.Label(
            start_label_row,
            text="开始",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).pack(side="left")
        self.start_var = tk.StringVar(value="00:00.000")
        self.start_entry = TimeEntry(
            start_block,
            self.start_var,
            width=9,
            on_submit=self._apply_time_entries,
        )
        self.start_entry.pack(anchor="w", pady=(3, 0))

        tk.Label(
            controls,
            text="→",
            bg=theme.SURFACE,
            fg=theme.MUTED_DARK,
            font=theme.Fonts.font(11),
        ).grid(row=0, column=1, padx=8, pady=(16, 0))

        end_block = tk.Frame(controls, bg=theme.SURFACE)
        end_block.grid(row=0, column=2, sticky="w")
        end_label_row = tk.Frame(end_block, bg=theme.SURFACE)
        end_label_row.pack(anchor="w")
        end_dot = tk.Canvas(
            end_label_row,
            width=7,
            height=7,
            bg=theme.SURFACE,
            highlightthickness=0,
            bd=0,
        )
        end_dot.pack(side="left", padx=(0, 5))
        end_dot.create_oval(1, 1, 6, 6, fill=theme.ORANGE, outline="")
        tk.Label(
            end_label_row,
            text="结束",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).pack(side="left")
        self.end_var = tk.StringVar(value="00:00.000")
        self.end_entry = TimeEntry(
            end_block,
            self.end_var,
            width=9,
            on_submit=self._apply_time_entries,
        )
        self.end_entry.pack(anchor="w", pady=(3, 0))

        divider = tk.Frame(controls, bg=theme.BORDER, width=1, height=36)
        divider.grid(row=0, column=3, padx=16, pady=(8, 0))

        length_block = tk.Frame(controls, bg=theme.SURFACE)
        length_block.grid(row=0, column=4, sticky="w")
        tk.Label(
            length_block,
            text="选段时长",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).pack(anchor="w")
        self.clip_length_label = tk.Label(
            length_block,
            text="00:00.000",
            bg=theme.SURFACE,
            fg=theme.TEXT,
            font=theme.Fonts.font(13, "bold", family=theme.Fonts.mono),
        )
        self.clip_length_label.pack(anchor="w", pady=(2, 0))

        self.preview_btn = RoundedButton(
            controls,
            text="▶  试听选段",
            command=self._preview_segment,
            style="primary",
            width=136,
            height=38,
            font_size=9,
            background=theme.SURFACE,
        )
        self.preview_btn.grid(row=0, column=7, sticky="e", padx=(10, 5), pady=(7, 0))
        self.stop_btn = RoundedButton(
            controls,
            text="■",
            command=self._stop_preview,
            style="ghost",
            width=38,
            height=38,
            font_size=9,
            background=theme.SURFACE,
            padx=6,
        )
        self.stop_btn.grid(row=0, column=8, sticky="e", pady=(7, 0))
        Tooltip(
            self.preview_btn,
            "Shift+点击波形，或点击底部时间标尺，设置试听起点",
        )

        quick = tk.Frame(body, bg=theme.SURFACE)
        quick.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        tk.Label(
            quick,
            text="快速选区",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).pack(side="left", padx=(0, 7))
        self.quick_buttons: list[RoundedButton] = []
        for seconds, label in ((30, "30 秒"), (60, "60 秒"), (90, "90 秒")):
            button = RoundedButton(
                quick,
                text=label,
                command=lambda value=seconds: self._quick_segment(value),
                style="quiet",
                width=50,
                height=27,
                font_size=8,
                bold=False,
                background=theme.SURFACE,
                padx=6,
            )
            button.pack(side="left", padx=(0, 3))
            self.quick_buttons.append(button)
        whole_btn = RoundedButton(
            quick,
            text="整首",
            command=lambda: self._quick_segment(None),
            style="quiet",
            width=46,
            height=27,
            font_size=8,
            bold=False,
            background=theme.SURFACE,
            padx=6,
        )
        whole_btn.pack(side="left")
        self.quick_buttons.append(whole_btn)
        self.segment_hint_label = tk.Label(
            quick,
            text="拖把手选段 · Shift+点击设试听起点 · 滚轮平移",
            bg=theme.SURFACE,
            fg=theme.MUTED_DARK,
            font=theme.Fonts.font(8),
            anchor="e",
        )
        self.segment_hint_label.pack(side="right")

        lower = tk.Frame(right, bg=theme.BG)
        lower.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        lower.grid_columnconfigure(0, weight=6, minsize=360)
        lower.grid_columnconfigure(1, weight=5, minsize=300)
        lower.grid_rowconfigure(0, weight=1)
        self._build_transition_card(lower)
        self._build_output_card(lower)

    def _build_transition_card(self, parent: tk.Misc) -> None:
        card = Card(parent, padding=13)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        body = card.body
        body.grid_columnconfigure(0, weight=1)

        header = tk.Frame(body, bg=theme.SURFACE)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(
            header,
            text="默认转场",
            bg=theme.SURFACE,
            fg=theme.TEXT,
            font=theme.Fonts.font(12, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            text="TRANSITION",
            bg=theme.SURFACE,
            fg=theme.MUTED_DARK,
            font=theme.Fonts.font(7, "bold"),
        ).pack(side="left", padx=(7, 0))
        self.insert_transition_var = tk.BooleanVar(value=True)
        self.insert_transition_check = ttk.Checkbutton(
            header,
            text="曲目之间",
            variable=self.insert_transition_var,
            command=self._on_transition_setting,
            style="App.TCheckbutton",
            takefocus=False,
        )
        self.insert_transition_check.pack(side="right")

        file_box = tk.Frame(
            body,
            bg=theme.INPUT,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            bd=0,
        )
        file_box.grid(row=1, column=0, sticky="ew", pady=(8, 7))
        file_box.grid_columnconfigure(1, weight=1)
        icon = tk.Canvas(
            file_box,
            width=30,
            height=30,
            bg=theme.INPUT,
            highlightthickness=0,
            bd=0,
        )
        icon.grid(row=0, column=0, rowspan=2, padx=(9, 6), pady=7)
        icon.create_oval(0, 0, 30, 30, fill=theme.ACCENT_SOFT, outline="")
        icon.create_text(
            15,
            15,
            text="♫",
            fill="#B9A8FF",
            font=theme.Fonts.font(13, "bold"),
        )
        self.transition_name_label = tk.Label(
            file_box,
            text="未选择转场音频",
            bg=theme.INPUT,
            fg=theme.TEXT_SOFT,
            font=theme.Fonts.font(9, "bold"),
            anchor="w",
        )
        self.transition_name_label.grid(row=0, column=1, sticky="sw", pady=(7, 0))
        self.transition_meta_label = tk.Label(
            file_box,
            text="选择音频后，会在歌曲衔接处自动插入",
            bg=theme.INPUT,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
            anchor="w",
        )
        self.transition_meta_label.grid(row=1, column=1, sticky="nw", pady=(1, 7))
        self.transition_preview_btn = RoundedButton(
            file_box,
            text="▶",
            command=self._toggle_transition_preview,
            style="quiet",
            width=30,
            height=28,
            font_size=8,
            background=theme.INPUT,
            padx=4,
        )
        self.transition_preview_btn.grid(row=0, column=2, rowspan=2, padx=(5, 3))
        self.transition_choose_btn = RoundedButton(
            file_box,
            text="选择",
            command=self._choose_transition,
            style="secondary",
            width=52,
            height=28,
            font_size=8,
            background=theme.INPUT,
            padx=7,
        )
        self.transition_choose_btn.grid(row=0, column=3, rowspan=2, padx=(2, 8))
        Tooltip(self.transition_name_label, "尚未选择转场音频")
        self._transition_tooltip = Tooltip(file_box, "尚未选择转场音频")

        settings = tk.Frame(body, bg=theme.SURFACE)
        settings.grid(row=2, column=0, sticky="ew")
        settings.grid_columnconfigure(1, weight=1)

        tk.Label(
            settings,
            text="时长",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).grid(row=0, column=0, sticky="w")
        self.transition_duration_var = tk.StringVar(value="2.5")
        self.transition_duration_entry = self._make_number_entry(
            settings, self.transition_duration_var, width=5
        )
        self.transition_duration_entry.grid(row=0, column=1, sticky="w", padx=(8, 4), pady=2)
        tk.Label(
            settings,
            text="秒",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).grid(row=0, column=2, sticky="w")
        self.full_var = tk.BooleanVar(value=False)
        self.full_check = ttk.Checkbutton(
            settings,
            text="完整播放",
            variable=self.full_var,
            command=self._on_transition_setting,
            style="App.TCheckbutton",
            takefocus=False,
        )
        self.full_check.grid(row=0, column=3, sticky="w", padx=(12, 0))
        self.transition_source_label = tk.Label(
            settings,
            text="",
            bg=theme.SURFACE,
            fg=theme.MUTED_DARK,
            font=theme.Fonts.font(8),
        )
        self.transition_source_label.grid(row=0, column=4, sticky="e")

        tk.Label(
            settings,
            text="音量",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).grid(row=1, column=0, sticky="w", pady=(7, 0))
        self.transition_volume_var = tk.DoubleVar(value=85)
        self.transition_volume_scale = ttk.Scale(
            settings,
            from_=0,
            to=150,
            orient="horizontal",
            variable=self.transition_volume_var,
            command=self._on_transition_volume,
            style="App.Horizontal.TScale",
            takefocus=False,
        )
        self.transition_volume_scale.grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(8, 6), pady=(7, 0)
        )
        self.transition_volume_scale.bind(
            "<ButtonRelease-1>", self._on_transition_volume_release, add="+"
        )
        self.transition_volume_label = tk.Label(
            settings,
            text="85%",
            bg=theme.SURFACE,
            fg=theme.TEXT_SOFT,
            font=theme.Fonts.font(8, family=theme.Fonts.mono),
            width=5,
            anchor="e",
        )
        self.transition_volume_label.grid(row=1, column=3, sticky="w", padx=(12, 0), pady=(7, 0))

        tk.Label(
            settings,
            text="淡化",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).grid(row=2, column=0, sticky="w", pady=(7, 0))
        self.transition_fade_var = tk.StringVar(value="0.18")
        self.transition_fade_entry = self._make_number_entry(
            settings, self.transition_fade_var, width=4
        )
        self.transition_fade_entry.grid(row=2, column=1, sticky="w", padx=(8, 4), pady=(7, 0))
        tk.Label(
            settings,
            text="秒",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).grid(row=2, column=2, sticky="w", pady=(7, 0))
        self.at_end_var = tk.BooleanVar(value=False)
        self.at_end_check = ttk.Checkbutton(
            settings,
            text="片尾也加",
            variable=self.at_end_var,
            command=self._on_transition_setting,
            style="App.TCheckbutton",
            takefocus=False,
        )
        self.at_end_check.grid(row=2, column=3, columnspan=2, sticky="w", padx=(12, 0), pady=(7, 0))

        self._transition_widgets = [
            self.insert_transition_check,
            self.transition_duration_entry,
            self.full_check,
            self.transition_volume_scale,
            self.transition_fade_entry,
            self.at_end_check,
            self.transition_preview_btn,
        ]

    def _make_number_entry(self, parent, variable: tk.StringVar, width: int) -> tk.Entry:
        entry = tk.Entry(
            parent,
            textvariable=variable,
            width=width,
            bg=theme.INPUT,
            fg=theme.TEXT,
            disabledbackground=theme.INPUT,
            disabledforeground=theme.MUTED_DARK,
            insertbackground=theme.ACCENT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            highlightcolor=theme.ACCENT,
            font=theme.Fonts.font(9, family=theme.Fonts.mono),
            justify="center",
        )
        entry.bind("<Return>", self._commit_transition_fields)
        entry.bind("<FocusOut>", self._commit_transition_fields)
        return entry

    def _build_output_card(self, parent: tk.Misc) -> None:
        card = Card(parent, padding=13)
        card.grid(row=0, column=1, sticky="nsew")
        body = card.body
        body.grid_columnconfigure(0, weight=1)

        header = tk.Frame(body, bg=theme.SURFACE)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(
            header,
            text="导出设置",
            bg=theme.SURFACE,
            fg=theme.TEXT,
            font=theme.Fonts.font(12, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            text="OUTPUT",
            bg=theme.SURFACE,
            fg=theme.MUTED_DARK,
            font=theme.Fonts.font(7, "bold"),
        ).pack(side="left", padx=(7, 0))
        self.open_output_btn = RoundedButton(
            header,
            text="输出目录",
            command=self._open_output_folder,
            style="quiet",
            width=66,
            height=26,
            font_size=8,
            bold=False,
            background=theme.SURFACE,
            padx=5,
        )
        self.open_output_btn.pack(side="right")
        self.normalize_var = tk.BooleanVar(value=False)
        self.normalize_check = ttk.Checkbutton(
            header,
            text="统一响度",
            variable=self.normalize_var,
            command=self._on_normalize_change,
            style="App.TCheckbutton",
            takefocus=False,
        )
        self.normalize_check.pack(side="right", padx=(0, 10))
        Tooltip(self.normalize_check, "导出时统一到约 -14 LUFS（单遍 loudnorm）")

        options = tk.Frame(body, bg=theme.SURFACE)
        options.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        options.grid_columnconfigure(1, weight=1)
        tk.Label(
            options,
            text="格式",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).grid(row=0, column=0, sticky="w")
        self.format_var = tk.StringVar(value="MP3")
        self.format_combo = ttk.Combobox(
            options,
            textvariable=self.format_var,
            values=list(FORMAT_CHOICES.keys()),
            state="readonly",
            style="App.TCombobox",
            width=10,
            font=theme.Fonts.font(9),
        )
        self.format_combo.grid(row=0, column=1, sticky="ew", padx=(7, 10))
        self.format_combo.bind("<<ComboboxSelected>>", self._on_format_change)
        tk.Label(
            options,
            text="音质",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).grid(row=0, column=2, sticky="w")
        self.bitrate_var = tk.StringVar(value="320k")
        self.bitrate_combo = ttk.Combobox(
            options,
            textvariable=self.bitrate_var,
            values=BITRATE_CHOICES,
            state="readonly",
            style="App.TCombobox",
            width=7,
            font=theme.Fonts.font(9),
        )
        self.bitrate_combo.grid(row=0, column=3, sticky="e", padx=(7, 0))
        self.bitrate_combo.bind("<<ComboboxSelected>>", self._on_bitrate_change)

        tk.Label(
            body,
            text="文件名",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.filename_var = tk.StringVar(value="随舞混剪.mp3")
        self.filename_entry = tk.Entry(
            body,
            textvariable=self.filename_var,
            bg=theme.INPUT,
            fg=theme.TEXT,
            disabledbackground=theme.INPUT,
            disabledforeground=theme.MUTED,
            readonlybackground=theme.INPUT,
            insertbackground=theme.ACCENT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            highlightcolor=theme.ACCENT,
            font=theme.Fonts.font(9),
        )
        self.filename_entry.grid(row=3, column=0, sticky="ew", pady=(3, 0), ipady=5)
        self.filename_entry.bind("<Return>", self._commit_filename)
        self.filename_entry.bind("<FocusOut>", self._commit_filename)

        summary = tk.Frame(
            body,
            bg=theme.INPUT,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            bd=0,
        )
        summary.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        summary.grid_columnconfigure(0, weight=1)
        tk.Label(
            summary,
            text="预计成片",
            bg=theme.INPUT,
            fg=theme.MUTED,
            font=theme.Fonts.font(7),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(4, 0))
        self.export_duration_label = tk.Label(
            summary,
            text="—",
            bg=theme.INPUT,
            fg=theme.TEXT,
            font=theme.Fonts.font(12, "bold", family=theme.Fonts.mono),
        )
        self.export_duration_label.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 4))
        self.export_detail_label = tk.Label(
            summary,
            text="0 段 · 0 次转场",
            bg=theme.INPUT,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
            anchor="e",
        )
        self.export_detail_label.grid(row=0, column=1, rowspan=2, sticky="e", padx=10)

    def _build_footer(self) -> None:
        footer = tk.Frame(self, bg=theme.BG)
        footer.grid(row=2, column=0, sticky="ew", padx=22, pady=(10, 12))
        bar = tk.Frame(
            footer,
            bg=theme.SURFACE,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            bd=0,
        )
        bar.pack(fill="x")
        inner = tk.Frame(bar, bg=theme.SURFACE)
        inner.pack(fill="x", padx=16, pady=8)
        inner.grid_columnconfigure(0, weight=1)

        status = tk.Frame(inner, bg=theme.SURFACE)
        status.grid(row=0, column=0, sticky="w")
        title_row = tk.Frame(status, bg=theme.SURFACE)
        title_row.pack(fill="x")
        self.status_dot = tk.Canvas(
            title_row,
            width=9,
            height=9,
            bg=theme.SURFACE,
            highlightthickness=0,
            bd=0,
        )
        self.status_dot.pack(side="left", padx=(0, 7))
        self.status_dot_id = self.status_dot.create_oval(1, 1, 8, 8, fill=theme.ACCENT, outline="")
        self.status_label = tk.Label(
            title_row,
            text="准备就绪",
            bg=theme.SURFACE,
            fg=theme.TEXT_SOFT,
            font=theme.Fonts.font(9, "bold"),
            anchor="w",
        )
        self.status_label.pack(side="left")
        self.footer_detail = tk.Label(
            status,
            text="打开一个文件夹，开始拼剪你的随舞歌单",
            bg=theme.SURFACE,
            fg=theme.MUTED,
            font=theme.Fonts.font(8),
            anchor="w",
        )
        self.footer_detail.pack(anchor="w")
        self.footer_progress = ProgressBar(status, height=5)
        self.footer_progress.pack(fill="x", pady=(6, 0))
        self.footer_progress.configure(width=300)

        self.cancel_btn = RoundedButton(
            inner,
            text="取消",
            command=self._cancel_export,
            style="danger",
            width=60,
            height=38,
            font_size=9,
            background=theme.SURFACE,
        )
        self.cancel_btn.grid(row=0, column=1, padx=(12, 8))
        self.cancel_btn.grid_remove()
        self.open_output_footer_btn = RoundedButton(
            inner,
            text="打开输出目录",
            command=self._open_output_folder,
            style="ghost",
            width=118,
            height=40,
            font_size=9,
            bold=False,
            background=theme.SURFACE,
        )
        self.open_output_footer_btn.grid(row=0, column=2, padx=(0, 8))
        self.export_btn = RoundedButton(
            inner,
            text="导出混剪",
            command=self._start_export,
            style="primary",
            width=138,
            height=40,
            font_size=10,
            background=theme.SURFACE,
        )
        self.export_btn.grid(row=0, column=3)

    # ------------------------------------------------------------------
    # Shortcuts / startup

    def _bind_shortcuts(self) -> None:
        self.bind_all("<Control-o>", lambda _e: self._open_folder_dialog())
        self.bind_all("<Control-O>", lambda _e: self._open_folder_dialog())
        self.bind_all("<Control-s>", lambda _e: self._save_now(announce=True))
        self.bind_all("<Control-S>", lambda _e: self._save_now(announce=True))
        self.bind_all("<Control-e>", lambda _e: self._start_export())
        self.bind_all("<Control-E>", lambda _e: self._start_export())
        self.bind_all("<F5>", lambda _e: self._rescan_folder())
        self.bind_all("<space>", self._on_space)
        self.bind_all("<Delete>", self._on_delete)
        self.bind_all("<Control-z>", self._on_undo_shortcut)
        self.bind_all("<Control-Z>", self._on_undo_shortcut)
        self.bind_all("<Control-y>", self._on_redo_shortcut)
        self.bind_all("<Control-Y>", self._on_redo_shortcut)
        self.bind_all("<Control-Shift-z>", self._on_redo_shortcut)
        self.bind_all("<Control-Shift-Z>", self._on_redo_shortcut)

    def _typing_focus(self) -> bool:
        widget = self.focus_get()
        return isinstance(widget, (tk.Entry, ttk.Combobox, tk.Text, ttk.Spinbox))

    def _on_space(self, _event=None):
        if self._typing_focus():
            return None
        if self.player.is_playing or self._preview_kind or self._preview_preparing:
            self._stop_preview()
        else:
            self._preview_segment()
        return "break"

    def _on_delete(self, _event=None):
        if self._typing_focus():
            return None
        self._remove_selected()
        return "break"

    def _on_undo_shortcut(self, _event=None):
        if self._typing_focus():
            return None
        self._undo()
        return "break"

    def _on_redo_shortcut(self, _event=None):
        if self._typing_focus():
            return None
        self._redo()
        return "break"

    def _startup(self) -> None:
        if self.project is not None:
            return
        args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
        if args:
            candidate = Path(args[0])
            if candidate.is_dir():
                self._open_folder(candidate)
                return
        if "--blank" in sys.argv:
            return
        last = self._settings.get("last_project")
        if last and Path(last).is_dir():
            self._open_folder(Path(last), quiet=True)
        elif self.project is None:
            self._set_status(
                "准备就绪",
                "打开一个文件夹，开始拼剪你的随舞歌单",
                "info",
            )

    # ------------------------------------------------------------------
    # Folder / project lifecycle

    def _open_folder_dialog(self) -> None:
        if self._export_running:
            messagebox.showinfo("正在导出", "请等待导出完成，或先取消导出。", parent=self)
            return
        folder = filedialog.askdirectory(
            title="选择歌单文件夹",
            mustexist=True,
            parent=self,
        )
        if folder:
            self._open_folder(Path(folder))

    def _open_folder(self, folder: Path, quiet: bool = False) -> None:
        if self._export_running:
            messagebox.showinfo("正在导出", "请等待导出完成，或先取消导出。", parent=self)
            return
        self._stop_preview()
        self._save_now()
        try:
            project = Project.open_folder(folder)
        except (ProjectError, OSError) as exc:
            if not quiet:
                messagebox.showerror("无法打开文件夹", str(exc), parent=self)
            self._set_status("无法打开文件夹", str(exc), "error")
            return
        self.project = project
        self.current_track = None
        self._probe_attempted.clear()
        self._wave_cache.clear()
        self._sort_key = None
        self._sort_reverse = False
        self._refresh_all()
        self._refresh_project_chip()
        self._refresh_transition()
        self._save_settings()
        self._save_now()
        self._start_probe_worker()
        self._ensure_transition_probed()
        tracks = self.project.enabled_tracks() or self.project.tracks
        if tracks:
            self._select_track(tracks[0])
        else:
            self._select_track(None)
        self._reset_history()
        read_warning = next(
            (warning for warning in self.project.warnings if "读取失败" in warning),
            None,
        )
        if read_warning:
            messagebox.showwarning("项目文件提示", read_warning, parent=self)
        if not self.project.tracks:
            self._set_status(
                "文件夹里没有歌曲",
                "支持 MP3 / WAV / FLAC / M4A 等格式，也可以点「＋ 添加」导入",
                "warn",
            )
            return
        detail = f"{len(self.project.tracks)} 首歌曲"
        if self.project.enabled_tracks():
            detail += f" · 已选 {len(self.project.enabled_tracks())} 首"
        if quiet:
            detail = "已恢复上次项目 · " + detail
        else:
            detail = "项目已打开 · " + detail
        self._set_status("准备就绪", detail, "success")

    def _rescan_folder(self) -> None:
        if not self.project:
            self._open_folder_dialog()
            return
        if self._export_running:
            messagebox.showinfo("正在导出", "请等待导出完成，或先取消导出。", parent=self)
            return
        folder = self.project.folder
        current = self.current_track
        self._open_folder(folder)
        if current and self.project:
            match = next((t for t in self.project.tracks if t.path == current.path), None)
            if match:
                self._select_track(match)
        self._set_status("已重新扫描", f"目录：{folder.name}", "success")

    def _refresh_project_chip(self) -> None:
        if not self.project:
            self.project_name_label.configure(text="未打开项目", fg=theme.TEXT_SOFT)
            self.project_detail_label.configure(text="选择一个歌单文件夹开始")
            self.project_chip.configure(highlightbackground=theme.BORDER)
            self.project_tooltip.update_text("尚未打开项目")
            return
        folder = self.project.folder
        name = folder.name or str(folder)
        if len(name) > 24:
            name = name[:23] + "…"
        self.project_name_label.configure(text=name, fg=theme.TEXT)
        parent_text = str(folder.parent)
        if len(parent_text) > 44:
            parent_text = "…" + parent_text[-43:]
        self.project_detail_label.configure(text=parent_text)
        self.project_chip.configure(highlightbackground=theme.BORDER)
        self.project_tooltip.update_text(str(folder))
        self._transition_tooltip.update_text(
            str(self.project.transition.path) if self.project.transition.path else "尚未选择转场音频"
        )

    def _reveal_project_folder(self) -> None:
        if self.project:
            try:
                os.startfile(str(self.project.folder))  # type: ignore[attr-defined]
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Playlist

    def _ensure_project(self) -> bool:
        if not self.project:
            messagebox.showinfo("还没有项目", "请先打开一个歌单文件夹。", parent=self)
            return False
        return True

    def _refresh_all(self) -> None:
        self._refresh_playlist()
        self._refresh_editor()
        self._refresh_transition()
        self._refresh_output()
        self._refresh_summary()
        self._refresh_action_states()
        self._refresh_project_chip()

    def _refresh_playlist(self, select: Track | None = None) -> None:
        if not hasattr(self, "tree"):
            return
        self._refreshing_tree = True
        try:
            self.tree.delete(*self.tree.get_children())
            if self.project:
                for index, track in enumerate(self.project.tracks):
                    iid = self._iid(track)
                    self.tree.insert(
                        "",
                        "end",
                        iid=iid,
                        values=self._track_values(index, track),
                        tags=(self._track_tag(track),),
                    )
            if not self.project or not self.project.tracks:
                self.empty_hint.configure(
                    text=(
                        "还没有歌曲\n点「＋ 添加」导入歌曲"
                        if self.project
                        else "还没有歌曲\n打开一个歌单文件夹开始"
                    )
                )
                self.empty_hint.place(relx=0.5, rely=0.44, anchor="center")
            else:
                self.empty_hint.place_forget()
            target = select or self.current_track
            if target and self.project and target in self.project.tracks:
                iid = self._iid(target)
                if self.tree.exists(iid):
                    self.tree.selection_set(iid)
                    self.tree.focus(iid)
                    self.tree.see(iid)
        finally:
            self._refreshing_tree = False
        self._refresh_counts()
        self._refresh_action_states()

    def _track_values(self, index: int, track: Track) -> tuple:
        duration = format_duration(track.duration) if track.duration > 0 else (
            "失败" if track.probe_error else "…"
        )
        if track.duration > 0:
            clip = f"{_short_time(track.start)}–{_short_time(track.end if track.end > 0 else track.duration)}"
        else:
            clip = "—"
        enabled = "✓" if track.enabled else "–"
        return (f"{index + 1:02d}", track.path.stem, duration, clip, enabled)

    def _track_tag(self, track: Track) -> str:
        if track.is_missing:
            return "missing"
        if track.probe_error:
            return "error"
        if not track.enabled:
            return "muted"
        return "normal"

    def _track_iid(self, track: Track) -> str:
        return f"track-{id(track)}"

    def _iid(self, track: Track) -> str:
        return self._track_iid(track)

    def _track_from_iid(self, iid: str) -> Track | None:
        if not self.project:
            return None
        return next((track for track in self.project.tracks if self._iid(track) == iid), None)

    def _update_tree_row(self, track: Track) -> None:
        if not hasattr(self, "tree") or not self.project:
            return
        try:
            index = self.project.tracks.index(track)
        except ValueError:
            return
        iid = self._iid(track)
        if self.tree.exists(iid):
            self.tree.item(iid, values=self._track_values(index, track), tags=(self._track_tag(track),))
        self._refresh_counts()
        self._refresh_summary()

    def _refresh_counts(self) -> None:
        if not self.project:
            self.count_badge.configure(text="0 首")
            return
        total = len(self.project.tracks)
        enabled = len(self.project.enabled_tracks())
        self.count_badge.configure(text=f"{total} 首 · {enabled} 已选")

    def _refresh_action_states(self) -> None:
        has_project = self.project is not None
        has_track = self.current_track is not None
        idx = self.project.tracks.index(self.current_track) if has_track and self.project else -1
        enabled = not self._export_running
        for button in (
            self.add_btn,
            self.name_sort_btn,
            self.duration_sort_btn,
            self.clip_sort_btn,
            self.open_output_btn,
            self.open_output_footer_btn,
        ):
            button.set_enabled(has_project and enabled)
        self.up_btn.set_enabled(has_track and idx > 0 and enabled)
        self.down_btn.set_enabled(
            has_track and self.project is not None and 0 <= idx < len(self.project.tracks) - 1 and enabled
        )
        self.toggle_btn.set_enabled(has_track and enabled)
        self.remove_btn.set_enabled(has_track and enabled)
        self.quick_buttons and [btn.set_enabled(has_track and enabled) for btn in self.quick_buttons]
        can_wave = has_track and self.wave.duration > 0 and enabled
        for button in (self.zoom_out_btn, self.zoom_in_btn, self.fit_wave_btn):
            button.set_enabled(can_wave)
        self.preview_btn.set_enabled(has_track and enabled and not self._preview_busy())
        self.stop_btn.set_enabled(has_track and enabled)
        if not self._preview_busy():
            self.preview_btn.set_text(self._preview_button_text())
        if has_track:
            self.toggle_btn.set_text("停用" if self.current_track.enabled else "启用")
        else:
            self.toggle_btn.set_text("停用")
        can_export = (
            has_project
            and bool(self.project.enabled_tracks())
            and all(track.duration > 0 and not track.is_missing for track in self.project.enabled_tracks())
            and not self._export_running
        )
        self.export_btn.set_enabled(can_export)
        if not self._export_running:
            self.export_btn.set_text("导出混剪")
        self.add_btn.set_enabled(has_project and enabled)
        self.open_output_footer_btn.set_enabled(has_project)
        self._refresh_history_buttons()

    def _refresh_history_buttons(self) -> None:
        if not hasattr(self, "undo_btn"):
            return
        available = self.project is not None and not self._export_running
        self.undo_btn.set_enabled(available and self.history.can_undo)
        self.redo_btn.set_enabled(available and self.history.can_redo)
        if hasattr(self, "_undo_tooltip"):
            self._undo_tooltip.update_text(
                f"撤销：{self.history.undo_label} (Ctrl+Z)"
                if self.history.can_undo
                else "撤销 (Ctrl+Z)"
            )
            self._redo_tooltip.update_text(
                f"重做：{self.history.redo_label} (Ctrl+Y)"
                if self.history.can_redo
                else "重做 (Ctrl+Y)"
            )

    def _history_path_key(self, path: Path) -> str:
        return self._path_key(path)

    def _capture_history_state(self) -> dict:
        if not self.project:
            return {}
        tracks = []
        for track in self.project.tracks:
            try:
                path = str(track.path.resolve())
            except OSError:
                path = str(track.path)
            tracks.append(
                {
                    "path": path,
                    "start": round(track.start, 3),
                    "end": round(track.end, 3),
                    "enabled": bool(track.enabled),
                    "duration": round(track.duration, 3),
                    "codec": track.codec,
                    "sample_rate": track.sample_rate,
                    "channels": track.channels,
                }
            )
        current = None
        if self.current_track is not None:
            try:
                current = str(self.current_track.path.resolve())
            except OSError:
                current = str(self.current_track.path)
        transition = self.project.transition
        try:
            transition_path = str(transition.path.resolve()) if transition.path else ""
        except OSError:
            transition_path = str(transition.path) if transition.path else ""
        return {
            "current": current,
            "tracks": tracks,
            "excluded": [str(path) for path in self.project.excluded_paths],
            "transition": {
                "path": transition_path,
                "source_duration": round(transition.source_duration, 3),
                "length": round(transition.length, 3),
                "full": bool(transition.full),
                "volume": round(transition.volume, 3),
                "fade": round(transition.fade, 3),
                "insert_between": bool(transition.insert_between),
                "at_end": bool(transition.at_end),
            },
            "output": {
                "fmt": self.project.output.fmt,
                "bitrate": self.project.output.bitrate,
                "filename": self.project.output.filename,
                "normalize": bool(self.project.output.normalize),
            },
        }

    def _reset_history(self) -> None:
        self.history.reset(self._capture_history_state() if self.project else None)
        self._refresh_history_buttons()

    def _sync_history_baseline(self) -> None:
        if (
            self.project
            and not self._restoring_history
            and not getattr(self, "_wave_dragging", False)
        ):
            self.history.sync(self._capture_history_state())

    def _record_history(self, label: str) -> None:
        if not self.project or self._restoring_history:
            return
        self.history.record(self._capture_history_state(), label)
        self._refresh_history_buttons()

    def _undo(self) -> None:
        if self._export_running:
            messagebox.showinfo("正在导出", "导出期间不能撤销，请等待完成或取消。", parent=self)
            return
        if not self.project or not self.history.can_undo:
            return
        self._stop_preview()
        entry = self.history.undo(self._capture_history_state())
        if entry is None:
            return
        try:
            self._restoring_history = True
            self._restore_history_state(entry.state)
        finally:
            self._restoring_history = False
        self.history.sync(self._capture_history_state())
        self._refresh_history_buttons()
        self._schedule_save()
        self._set_status("已撤销", entry.label, "info")

    def _redo(self) -> None:
        if self._export_running:
            messagebox.showinfo("正在导出", "导出期间不能重做，请等待完成或取消。", parent=self)
            return
        if not self.project or not self.history.can_redo:
            return
        self._stop_preview()
        entry = self.history.redo(self._capture_history_state())
        if entry is None:
            return
        try:
            self._restoring_history = True
            self._restore_history_state(entry.state)
        finally:
            self._restoring_history = False
        self.history.sync(self._capture_history_state())
        self._refresh_history_buttons()
        self._schedule_save()
        self._set_status("已重做", entry.label, "info")

    def _restore_history_state(self, state: dict) -> None:
        if not self.project or not state:
            return
        old_transition = self.project.transition
        old_transition_key = (
            self._history_path_key(old_transition.path) if old_transition.path else ""
        )
        current_by_key = {
            self._history_path_key(track.path): track for track in self.project.tracks
        }
        new_tracks: list[Track] = []
        for item in state.get("tracks", []):
            path = Path(item["path"])
            key = self._history_path_key(path)
            existing = current_by_key.get(key)
            if existing is not None:
                existing.start = float(item.get("start") or 0.0)
                existing.end = float(item.get("end") or 0.0)
                existing.enabled = bool(item.get("enabled", True))
                existing.normalise()
                new_tracks.append(existing)
            else:
                track = Track(
                    path=path,
                    duration=max(0.0, float(item.get("duration") or 0.0)),
                    start=max(0.0, float(item.get("start") or 0.0)),
                    end=max(0.0, float(item.get("end") or 0.0)),
                    enabled=bool(item.get("enabled", True)),
                    codec=str(item.get("codec") or ""),
                    sample_rate=int(item.get("sample_rate") or 0),
                    channels=int(item.get("channels") or 0),
                )
                track.normalise()
                new_tracks.append(track)
        self.project.tracks = new_tracks
        for track in new_tracks:
            if track.duration <= 0:
                self._probe_attempted.discard(self._history_path_key(track.path))

        transition_state = state.get("transition") or {}
        transition_path_value = transition_state.get("path") or ""
        transition_path = Path(transition_path_value) if transition_path_value else None
        transition_key = (
            self._history_path_key(transition_path) if transition_path else ""
        )
        source_duration = float(transition_state.get("source_duration") or 0.0)
        if old_transition.path and transition_key == old_transition_key:
            source_duration = max(source_duration, old_transition.source_duration)
        old_transition.path = transition_path
        old_transition.source_duration = source_duration
        old_transition.length = float(transition_state.get("length") or 2.5)
        old_transition.full = bool(transition_state.get("full", False))
        old_transition.volume = float(transition_state.get("volume") or 0.85)
        old_transition.fade = float(transition_state.get("fade") or 0.18)
        old_transition.insert_between = bool(transition_state.get("insert_between", True))
        old_transition.at_end = bool(transition_state.get("at_end", False))
        old_transition.validate()

        output_state = state.get("output") or {}
        self.project.output.fmt = str(output_state.get("fmt") or "mp3")
        self.project.output.bitrate = str(output_state.get("bitrate") or "320k")
        self.project.output.filename = str(output_state.get("filename") or "")
        self.project.output.normalize = bool(output_state.get("normalize", False))
        self.project.excluded_paths = [
            Path(value) for value in state.get("excluded", []) if value
        ]
        self.project.dirty = True
        self.current_track = None
        self._sort_key = None
        self._sort_reverse = False
        self._reset_sort_buttons()
        wanted = state.get("current")
        target = None
        if wanted:
            wanted_key = self._history_path_key(Path(wanted))
            target = next(
                (
                    track
                    for track in self.project.tracks
                    if self._history_path_key(track.path) == wanted_key
                ),
                None,
            )
        self._refresh_all()
        if target is None and self.project.tracks:
            target = self.project.enabled_tracks()[0] if self.project.enabled_tracks() else self.project.tracks[0]
        self._select_track(target)
        self._refresh_summary()
        self._start_probe_worker()
        self._ensure_transition_probed()

    def _preview_busy(self) -> bool:
        return self._preview_preparing or self.player.is_playing

    def _on_tree_select(self, _event=None) -> None:
        if self._refreshing_tree:
            return
        selection = self.tree.selection()
        if not selection:
            return
        track = self._track_from_iid(selection[0])
        if track and track is not self.current_track:
            self._stop_preview()
            self.current_track = track
            self._refresh_editor()
            self._load_waveform(track)
            self._refresh_action_states()

    def _select_track(self, track: Track | None) -> None:
        self._stop_preview()
        self.current_track = track
        if track and self.project:
            iid = self._iid(track)
            if self.tree.exists(iid):
                self._refreshing_tree = True
                try:
                    self.tree.selection_set(iid)
                    self.tree.focus(iid)
                    self.tree.see(iid)
                finally:
                    self._refreshing_tree = False
        self._refresh_editor()
        self._refresh_action_states()
        if track:
            self._load_waveform(track)

    def _on_tree_press(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        self._drag_iid = iid or None
        self._drag_start_y = event.y
        self._drag_started = False
        if iid:
            track = self._track_from_iid(iid)
            if track and track is not self.current_track:
                self._stop_preview()
                self.current_track = track
                self._refresh_editor()
                self._load_waveform(track)
                self._refresh_action_states()
            self._refreshing_tree = True
            try:
                self.tree.selection_set(iid)
                self.tree.focus(iid)
            finally:
                self._refreshing_tree = False

    def _on_tree_motion(self, event) -> None:
        if not self._drag_iid:
            return
        if abs(event.y - self._drag_start_y) > 6:
            self._drag_started = True
            self.tree.configure(cursor="fleur")

    def _on_tree_release(self, event) -> None:
        self.tree.configure(cursor="")
        if not self.project or not self._drag_iid:
            self._drag_iid = None
            self._drag_started = False
            return
        source = self._track_from_iid(self._drag_iid)
        column = self.tree.identify_column(event.x)
        target_iid = self.tree.identify_row(event.y)
        dragging = self._drag_started
        self._drag_iid = None
        self._drag_started = False
        if dragging and source and target_iid:
            target = self._track_from_iid(target_iid)
            if target and target is not source:
                source_index = self.project.tracks.index(source)
                target_index = self.project.tracks.index(target)
                if self.project.move_track(source_index, target_index):
                    self._sort_key = None
                    self._sort_reverse = False
                    self._reset_sort_buttons()
                    self._refresh_playlist(select=source)
                    self._refresh_summary()
                    self._schedule_save()
                    self._record_history("拖拽排序")
                    self._set_status("顺序已更新", f"{source.name} → 第 {target_index + 1} 位", "info")
            return
        if column == "#5" and source:
            self._toggle_track(source)

    def _on_tree_double_click(self, event) -> None:
        if self.tree.identify_column(event.x) == "#5":
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        track = self._track_from_iid(iid)
        if track:
            self._toggle_track(track)

    def _on_tree_context(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        track = self._track_from_iid(iid)
        if not track:
            return
        self._select_track(track)
        menu = tk.Menu(
            self,
            tearoff=0,
            bg=theme.SURFACE_ALT,
            fg=theme.TEXT_SOFT,
            activebackground=theme.ACCENT_DEEP,
            activeforeground=theme.WHITE,
            bd=0,
            font=theme.Fonts.font(9),
        )
        menu.add_command(label="试听选段  Space", command=self._preview_segment)
        menu.add_separator()
        menu.add_command(
            label="停用此曲" if track.enabled else "启用此曲",
            command=lambda: self._toggle_track(track),
        )
        menu.add_command(label="设为整首", command=lambda: self._set_whole_track(track))
        if track.probe_error:
            menu.add_command(label="重新读取音频", command=lambda: self._retry_probe(track))
        menu.add_separator()
        menu.add_command(label="在资源管理器中显示", command=lambda: self._reveal_track(track))
        menu.add_command(label="从项目移除", command=lambda: self._remove_track(track))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _toggle_selected(self) -> None:
        if self.current_track:
            self._toggle_track(self.current_track)

    def _toggle_track(self, track: Track) -> None:
        if not self.project:
            return
        track.enabled = not track.enabled
        self.project.dirty = True
        self._update_tree_row(track)
        self._refresh_summary()
        self._refresh_action_states()
        self._schedule_save()
        self._record_history("启用或停用歌曲")
        state = "启用" if track.enabled else "停用"
        self._set_status(f"已{state}", track.name, "info")

    def _set_whole_track(self, track: Track) -> None:
        if track.duration <= 0:
            return
        track.start = 0.0
        track.end = track.duration
        self.project.dirty = True  # type: ignore[union-attr]
        self._update_tree_row(track)
        if track is self.current_track:
            self._refresh_editor()
            self._load_waveform(track)
        self._schedule_save()
        self._record_history("设为整首")

    def _remove_selected(self) -> None:
        if self.current_track:
            self._remove_track(self.current_track)

    def _remove_track(self, track: Track) -> None:
        if not self.project:
            return
        if not messagebox.askyesno(
            "从项目移除",
            f"从歌单移除「{track.name}」？\n\n音频文件本身不会被删除。",
            parent=self,
        ):
            return
        index = self.project.tracks.index(track)
        self.project.remove_track(track)
        self.current_track = None
        self._refresh_playlist()
        remaining = self.project.tracks
        if remaining:
            next_index = min(index, len(remaining) - 1)
            self._select_track(remaining[next_index])
        else:
            self._select_track(None)
        self._refresh_summary()
        self._schedule_save()
        self._record_history("移除歌曲")
        self._set_status("已从项目移除", track.name, "info")

    def _move_selected(self, delta: int) -> None:
        if not self.project or not self.current_track:
            return
        index = self.project.tracks.index(self.current_track)
        target = index + delta
        if self.project.move_track(index, target):
            self._sort_key = None
            self._sort_reverse = False
            self._reset_sort_buttons()
            self._refresh_playlist(select=self.current_track)
            self._refresh_summary()
            self._schedule_save()
            self._record_history("调整歌曲顺序")
            self._set_status(
                "顺序已更新",
                f"{self.current_track.name} → 第 {target + 1} 位",
                "info",
            )

    def _sort_tracks(self, key: str) -> None:
        if not self.project:
            return
        if self._sort_key == key:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_key = key
            self._sort_reverse = False
        self.project.sort_tracks(key, reverse=self._sort_reverse)
        self.project.dirty = True
        self._update_sort_buttons()
        self._refresh_playlist(select=self.current_track)
        self._refresh_summary()
        self._schedule_save()
        self._record_history("排序歌曲")
        label = {"name": "文件名", "duration": "歌曲时长", "clip": "选段时长"}[key]
        order = "降序" if self._sort_reverse else "升序"
        self._set_status("已重新排序", f"按{label}{order}", "info")

    def _update_sort_buttons(self) -> None:
        mapping = {
            "name": (self.name_sort_btn, "名称"),
            "duration": (self.duration_sort_btn, "时长"),
            "clip": (self.clip_sort_btn, "选段"),
        }
        for key, (button, label) in mapping.items():
            if self._sort_key == key:
                button.set_text(f"{label} {'↓' if self._sort_reverse else '↑'}")
                button.set_style("secondary")
            else:
                button.set_text(label)
                button.set_style("quiet")

    def _reset_sort_buttons(self) -> None:
        self.name_sort_btn.set_text("名称")
        self.duration_sort_btn.set_text("时长")
        self.clip_sort_btn.set_text("选段")
        for button in (self.name_sort_btn, self.duration_sort_btn, self.clip_sort_btn):
            button.set_style("quiet")

    def _retry_probe(self, track: Track) -> None:
        track.probe_error = ""
        self._probe_attempted.discard(self._path_key(track.path))
        self._update_tree_row(track)
        self._start_probe_worker()

    def _path_key(self, path: Path) -> str:
        try:
            return os.path.normcase(str(path.resolve())).casefold()
        except OSError:
            return os.path.normcase(str(path)).casefold()

    # ------------------------------------------------------------------
    # Track importing / probing

    def _on_empty_hint_click(self, _event=None) -> None:
        if self.project:
            self._add_files()
        else:
            self._open_folder_dialog()

    def _add_files(self) -> None:
        if not self._ensure_project():
            return
        filename = filedialog.askopenfilenames(
            title="添加歌曲",
            parent=self,
            filetypes=[
                ("音频文件", " ".join(f"*{ext}" for ext in sorted(AUDIO_EXTENSIONS))),
                ("所有文件", "*.*"),
            ],
        )
        if not filename:
            return
        added = self.project.add_paths(list(filename))  # type: ignore[union-attr]
        if not added:
            self._set_status("没有添加新歌曲", "这些文件已在项目中", "info")
            return
        for track in added:
            self._probe_attempted.discard(self._path_key(track.path))
        self._refresh_playlist(select=added[0])
        self._select_track(added[0])
        self._refresh_summary()
        self._start_probe_worker()
        self._schedule_save()
        self._record_history("添加歌曲")
        self._set_status("已添加歌曲", f"新增 {len(added)} 首", "success")

    def _start_probe_worker(self) -> None:
        if not self.project or self._probing:
            return
        pending = [
            track
            for track in self.project.tracks
            if track.duration <= 0
            and not track.probe_error
            and self._path_key(track.path) not in self._probe_attempted
            and track.path.is_file()
        ]
        if not pending:
            return
        for track in pending:
            self._probe_attempted.add(self._path_key(track.path))
        self._probing = True

        def worker() -> None:
            for track in pending:
                try:
                    info = probe_audio(track.path)
                except Exception as exc:
                    self.events.put(("probe_error", (track, str(exc))))
                else:
                    self.events.put(("probe_done", (track, info)))
            self.events.put(("probe_finished", None))

        threading.Thread(target=worker, name="audio-probe", daemon=True).start()
        self._set_status("正在读取歌曲", f"分析 {len(pending)} 个音频文件…", "busy")

    def _ensure_transition_probed(self) -> None:
        if not self.project:
            return
        transition = self.project.transition
        if transition.path and transition.path.is_file() and transition.source_duration <= 0:
            path = transition.path

            def worker() -> None:
                try:
                    info = probe_audio(path)
                except Exception as exc:
                    self.events.put(("transition_probe_error", (path, str(exc))))
                else:
                    self.events.put(("transition_probe_done", (path, info)))

            threading.Thread(target=worker, name="transition-probe", daemon=True).start()

    # ------------------------------------------------------------------
    # Waveform

    def _wave_key(self, track: Track) -> tuple:
        try:
            stat = track.path.stat()
            return (str(track.path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            return (str(track.path), 0, 0)

    def _load_waveform(self, track: Track) -> None:
        if not track:
            self.wave.clear("选择歌曲后显示波形")
            return
        if track.is_missing:
            self.wave.clear("音频文件缺失，请重新添加")
            return
        key = self._wave_key(track)
        cached = self._wave_cache.get(key)
        if cached is not None:
            self._wave_cache.move_to_end(key)
            waveform = cached
            if track.duration <= 0:
                track.duration = waveform.duration
                if track.end <= 0:
                    track.start = 0.0
                    track.end = waveform.duration
                self._update_tree_row(track)
            self.wave.set_data(
                waveform.peaks,
                waveform.duration,
                track.start,
                track.end if track.end > 0 else waveform.duration,
                reset_view=True,
            )
            self._refresh_editor_labels_only()
            self._refresh_action_states()
            return
        self._wave_token += 1
        token = self._wave_token
        self.wave.set_loading("正在分析波形…")
        path = track.path

        def worker() -> None:
            try:
                waveform = load_waveform(path, buckets=4000)
            except Exception as exc:
                self.events.put(("wave_error", (token, track, str(exc))))
            else:
                self.events.put(("wave_done", (token, track, waveform)))

        threading.Thread(target=worker, name="waveform", daemon=True).start()

    def _handle_wave_done(self, payload) -> None:
        token, track, waveform = payload
        if token != self._wave_token or track is not self.current_track:
            return
        key = self._wave_key(track)
        self._wave_cache[key] = waveform
        self._wave_cache.move_to_end(key)
        while len(self._wave_cache) > 8:
            self._wave_cache.popitem(last=False)
        if track.duration <= 0:
            track.duration = waveform.duration
            track.probe_error = ""
            if track.end <= 0:
                track.start = 0.0
                track.end = waveform.duration
            self.wave.set_data(
                waveform.peaks,
                waveform.duration,
                track.start,
                track.end,
                reset_view=True,
            )
            self._refresh_playlist(select=track)
            self._refresh_editor()
            self._schedule_save()
        else:
            self.wave.set_data(
                waveform.peaks,
                waveform.duration,
                track.start,
                track.end if track.end > 0 else waveform.duration,
                reset_view=True,
            )
        self._refresh_editor_labels_only()
        self._refresh_action_states()
        self._refresh_summary()
        self._sync_history_baseline()

    def _handle_wave_error(self, payload) -> None:
        token, track, error = payload
        if token != self._wave_token or track is not self.current_track:
            return
        self.wave.clear("无法读取波形")
        self._set_status("波形读取失败", error, "error")

    def _refresh_editor_labels_only(self) -> None:
        track = self.current_track
        if not track:
            return
        meta = track.metadata_text() or "音频"
        if track.duration > 0:
            meta = f"{meta} · 总长 {format_duration(track.duration, precise=True)}"
        self.track_meta_label.configure(text=meta)

    def _on_wave_view_change(self, start: float, end: float, duration: float) -> None:
        if not hasattr(self, "wave_hscroll"):
            return
        if duration <= 0:
            self.wave_hscroll.set(0, 1)
            self.wave_hscroll.state(["disabled"])
            return
        first = max(0.0, min(1.0, start / duration))
        last = max(first, min(1.0, end / duration))
        self.wave_hscroll.set(first, last)
        if last - first >= 0.9999:
            self.wave_hscroll.state(["disabled"])
        else:
            self.wave_hscroll.state(["!disabled"])

    def _on_wave_scrollbar(self, action: str, *args) -> None:
        if self.current_track is None or self.wave.duration <= 0:
            return
        if action == "moveto" and args:
            try:
                self.wave.scroll_fraction(float(args[0]))
            except (TypeError, ValueError):
                return
        elif action == "scroll" and len(args) >= 2:
            try:
                amount = int(args[0])
            except (TypeError, ValueError):
                return
            view_length = self.wave.view_range()[1] - self.wave.view_range()[0]
            step = view_length * (0.9 if args[1] == "pagesize" else 0.1)
            self.wave.pan(amount * step)

    def _zoom_wave(self, factor: float) -> None:
        if self.current_track is None or self.wave.duration <= 0:
            return
        self.wave.zoom(factor)
        self._refresh_action_states()

    def _fit_wave(self) -> None:
        if self.current_track is None:
            return
        self.wave.fit_view()
        self._refresh_action_states()

    def _on_wave_audition(self, seconds: float | None) -> None:
        if not hasattr(self, "preview_btn"):
            return
        if not self._preview_preparing and not self.player.is_playing:
            self.preview_btn.set_text(self._preview_button_text())
        if seconds is not None:
            self._flash_hint(
                f"试听起点 {format_duration(seconds, precise=True)} · 按空格或试听按钮开始"
            )

    def _preview_button_text(self) -> str:
        if hasattr(self, "wave") and self.wave.audition_time() is not None:
            return "▶  从此处试听"
        return "▶  试听选段"

    def _on_wave_context(self, seconds: float, x_root: int, y_root: int) -> None:
        menu = tk.Menu(
            self,
            tearoff=0,
            bg=theme.SURFACE_ALT,
            fg=theme.TEXT_SOFT,
            activebackground=theme.ACCENT_DEEP,
            activeforeground=theme.WHITE,
            bd=0,
            font=theme.Fonts.font(9),
        )
        menu.add_command(
            label="从此处试听",
            command=lambda: self._play_from_time(seconds),
        )
        if self.wave.audition_time() is not None:
            menu.add_command(
                label="清除试听起点",
                command=lambda: self.wave.clear_audition(),
            )
        menu.add_separator()
        menu.add_command(label="全览波形", command=self._fit_wave)
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    def _play_from_time(self, seconds: float) -> None:
        self.wave.set_audition(seconds)
        self._preview_segment()

    # ------------------------------------------------------------------
    # Editor

    def _refresh_editor(self) -> None:
        track = self.current_track
        self._loading_ui = True
        try:
            if not track:
                self.track_title_label.configure(text="选择一首歌开始")
                self.track_meta_label.configure(
                    text="在左侧歌单中选择歌曲，然后拖出要剪进混剪的片段"
                )
                self.track_state_badge.configure(text="未选择", bg=theme.ACCENT_SOFT, fg="#C3B3FF")
                self.start_var.set("00:00.000")
                self.end_var.set("00:00.000")
                self.clip_length_label.configure(text="00:00.000", fg=theme.MUTED)
                self.wave.clear("选择歌曲后显示波形")
                self._set_time_entries_enabled(False)
                return
            name = track.name
            self.track_title_label.configure(text=self._elide(name, 46))
            meta_parts = [track.metadata_text() or "音频文件"]
            if track.duration > 0:
                meta_parts.append(f"总长 {format_duration(track.duration, precise=True)}")
            if track.is_missing:
                meta_parts.append("文件缺失")
            self.track_meta_label.configure(text=" · ".join(meta_parts))
            if track.duration > 0 and abs((track.end - track.start) - track.duration) < 0.25:
                self.track_state_badge.configure(text="整首", bg=theme.MINT_DEEP, fg=theme.MINT)
            elif track.duration > 0:
                self.track_state_badge.configure(text="已裁剪", bg=theme.ACCENT_SOFT, fg="#C3B3FF")
            else:
                self.track_state_badge.configure(text="读取中", bg=theme.ORANGE_DEEP, fg=theme.ORANGE)
            self.start_var.set(format_duration(track.start, precise=True))
            end_value = track.end if track.end > 0 else track.duration
            self.end_var.set(format_duration(end_value, precise=True))
            self.clip_length_label.configure(
                text=format_duration(track.clip_duration, precise=True),
                fg=theme.TEXT,
            )
            self._set_time_entries_enabled(True)
        finally:
            self._loading_ui = False
        if track and track.is_missing:
            self.wave.clear("音频文件缺失，请重新添加")
        elif track and self._wave_key(track) in self._wave_cache:
            waveform = self._wave_cache[self._wave_key(track)]
            self.wave.set_data(
                waveform.peaks,
                waveform.duration,
                track.start,
                track.end if track.end > 0 else waveform.duration,
            )
        self._refresh_editor_labels_only()

    def _elide(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1] + "…"

    def _set_time_entries_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.start_entry.entry.configure(state=state)
        self.end_entry.entry.configure(state=state)
        self.preview_btn.set_enabled(enabled and not self._preview_busy())
        self.stop_btn.set_enabled(enabled)
        for button in self.quick_buttons:
            button.set_enabled(enabled)

    def _on_wave_change(self, start: float, end: float, final: bool) -> None:
        if self._loading_ui or not self.current_track:
            return
        track = self.current_track
        track.start = max(0.0, min(start, track.duration - 0.15))
        track.end = max(track.start + 0.15, min(end, track.duration))
        self.start_var.set(format_duration(track.start, precise=True))
        self.end_var.set(format_duration(track.end, precise=True))
        self.clip_length_label.configure(text=format_duration(track.clip_duration, precise=True))
        if track.duration > 0:
            if abs(track.clip_duration - track.duration) < 0.25:
                self.track_state_badge.configure(text="整首", bg=theme.MINT_DEEP, fg=theme.MINT)
            else:
                self.track_state_badge.configure(text="已裁剪", bg=theme.ACCENT_SOFT, fg="#C3B3FF")
        iid = self._iid(track)
        if self.tree.exists(iid):
            self.tree.set(iid, "clip", f"{_short_time(track.start)}–{_short_time(track.end)}")
        self._refresh_summary()
        if final:
            self._wave_dragging = False
            self._stop_preview()
            self.project.dirty = True  # type: ignore[union-attr]
            self._schedule_save()
            self._record_history("调整选段")
        else:
            self._wave_dragging = True

    def _apply_time_entries(self) -> str:
        if not self.current_track or not self.project:
            return "break"
        track = self.current_track
        if track.duration <= 0:
            self._flash_hint("歌曲信息还在读取中，请稍候")
            self._refresh_editor()
            return "break"
        try:
            start = parse_time(self.start_var.get())
            end = parse_time(self.end_var.get())
        except (ValueError, TypeError):
            self._flash_hint("时间格式应为 01:23.500", error=True)
            self._refresh_editor()
            return "break"
        start = min(max(0.0, start), track.duration - 0.15)
        end = min(max(track.duration and 0.15 or 0.15, end), track.duration)
        if end <= start:
            end = min(track.duration, start + 0.15)
            if end <= start:
                start = max(0.0, end - 0.15)
        track.start = round(start, 3)
        track.end = round(end, 3)
        self.project.dirty = True
        self.wave.set_selection(track.start, track.end)
        self._stop_preview()
        self._refresh_editor()
        self._update_tree_row(track)
        self._refresh_summary()
        self._schedule_save()
        self._record_history("输入选段时间")
        self._flash_hint("选段已更新")
        return "break"

    def _quick_segment(self, seconds: int | None) -> None:
        if not self.current_track or not self.project:
            return
        track = self.current_track
        if track.duration <= 0:
            return
        if seconds is None:
            track.start = 0.0
            track.end = track.duration
        else:
            length = min(float(seconds), track.duration)
            start = min(track.start, max(0.0, track.duration - length))
            track.start = round(start, 3)
            track.end = round(start + length, 3)
        self.project.dirty = True
        self._stop_preview()
        self.wave.set_selection(track.start, track.end)
        self._refresh_editor()
        self._update_tree_row(track)
        self._refresh_summary()
        self._schedule_save()
        self._record_history("快速选区")
        label = "整首" if seconds is None else f"{seconds} 秒"
        self._flash_hint(f"已设为{label}")

    def _flash_hint(self, text: str, error: bool = False) -> None:
        if self._hint_after_id:
            try:
                self.after_cancel(self._hint_after_id)
            except tk.TclError:
                pass
            self._hint_after_id = None
        self.segment_hint_label.configure(
            text=text,
            fg=theme.RED if error else theme.MINT,
        )
        self._hint_after_id = self.after(2600, self._reset_hint)

    def _reset_hint(self) -> None:
        self._hint_after_id = None
        if hasattr(self, "segment_hint_label"):
            self.segment_hint_label.configure(
                text="拖把手选段 · Shift+点击设试听起点 · 滚轮平移",
                fg=theme.MUTED_DARK,
            )

    # ------------------------------------------------------------------
    # Preview

    def _preview_segment(self) -> None:
        if not self.current_track or not self.project:
            return
        if self.player.is_playing or self._preview_kind is not None:
            self._stop_preview()
        track = self.current_track
        if track.duration <= 0 or track.clip_duration <= 0:
            self._set_status("无法试听", "歌曲信息尚未读取完成", "error")
            return
        if track.is_missing:
            self._set_status("无法试听", "音频文件缺失", "error")
            return
        self._stop_preview()
        self._preview_token += 1
        token = self._preview_token
        audition = self.wave.audition_time()
        if audition is not None and track.start <= audition < track.end:
            start = audition
        else:
            start = track.start
        end = track.end
        destination = self._temp_dir / f"segment_{token}.wav"
        self._preview_kind = "segment"
        self._preview_preparing = True
        self._preview_duration = end - start
        self._preview_start = start
        self.preview_btn.set_text("准备中…")
        self.preview_btn.set_enabled(False)
        self.stop_btn.set_enabled(True)
        self._set_status("正在准备试听", f"{track.name} · {format_duration(start, precise=True)} → {format_duration(end, precise=True)}", "busy")

        def worker() -> None:
            try:
                render_preview(track.path, destination, start=start, end=end)
            except Exception as exc:
                self.events.put(("preview_error", (token, str(exc))))
            else:
                self.events.put(("preview_ready", (token, destination, "segment")))

        threading.Thread(target=worker, name="preview-render", daemon=True).start()

    def _toggle_transition_preview(self) -> None:
        if self._preview_kind == "transition" or self.player.is_playing:
            self._stop_preview()
            return
        self._preview_transition()

    def _preview_transition(self) -> None:
        if not self.project:
            return
        transition = self.project.transition
        if not transition.path or not transition.path.is_file():
            self._set_status("无法试听转场", "请先选择转场音频", "error")
            return
        if self.player.is_playing or self._preview_kind is not None:
            self._stop_preview()
        self._preview_token += 1
        token = self._preview_token
        destination = self._temp_dir / f"transition_{token}.wav"
        length = transition.clip_length
        if transition.source_duration > 0:
            length = min(length if not transition.full else transition.source_duration, transition.source_duration)
        self._preview_kind = "transition"
        self._preview_preparing = True
        self._preview_duration = max(0.05, length)
        self.transition_preview_btn.set_text("…")
        self.transition_preview_btn.set_enabled(False)
        self._set_status("正在准备转场试听", transition.path.name, "busy")

        def worker() -> None:
            try:
                render_preview(
                    transition.path,
                    destination,
                    start=0.0,
                    end=self._preview_duration,
                    volume=transition.volume,
                )
            except Exception as exc:
                self.events.put(("preview_error", (token, str(exc))))
            else:
                self.events.put(("preview_ready", (token, destination, "transition")))

        threading.Thread(target=worker, name="preview-transition", daemon=True).start()

    def _handle_preview_ready(self, payload) -> None:
        token, destination, kind = payload
        if token != self._preview_token:
            return
        self._preview_preparing = False
        try:
            self.player.play(destination, self._preview_duration)
        except Exception as exc:
            self._preview_kind = None
            self._reset_preview_controls()
            self._set_status("试听失败", str(exc), "error")
            return
        if kind == "segment":
            self.preview_btn.set_text("试听中…")
            self.preview_btn.set_enabled(False)
            self.stop_btn.set_enabled(True)
            self._set_status("正在试听选段", self.current_track.name if self.current_track else "", "busy")
        else:
            self.transition_preview_btn.set_text("■")
            self.transition_preview_btn.set_enabled(True)
            self._set_status("正在试听转场", self.project.transition.path.name if self.project and self.project.transition.path else "", "busy")

    def _handle_preview_error(self, payload) -> None:
        token, error = payload
        if token != self._preview_token:
            return
        self._preview_kind = None
        self._preview_preparing = False
        self._preview_duration = 0.0
        self._reset_preview_controls()
        self._set_status("试听准备失败", error, "error")

    def _on_playback_tick(self, elapsed: float) -> None:
        if self._preview_kind == "segment" and self.current_track:
            self.wave.set_playhead(self._preview_start + elapsed)

    def _on_playback_finished(self) -> None:
        self._preview_kind = None
        self._preview_preparing = False
        self._preview_duration = 0.0
        self.wave.set_playhead(None)
        self._reset_preview_controls()
        self._set_status("准备就绪", "试听结束", "info")

    def _reset_preview_controls(self) -> None:
        self._preview_preparing = False
        if hasattr(self, "preview_btn"):
            self.preview_btn.set_text(self._preview_button_text())
            self.preview_btn.set_enabled(self.current_track is not None and not self._export_running)
            self.stop_btn.set_enabled(self.current_track is not None)
        if hasattr(self, "transition_preview_btn"):
            self.transition_preview_btn.set_text("▶")
            enabled = bool(
                self.project
                and self.project.transition.path
                and self.project.transition.path.is_file()
            )
            self.transition_preview_btn.set_enabled(enabled)
        self.wave.set_playhead(None)

    def _stop_preview(self) -> None:
        self._preview_token += 1
        self._preview_kind = None
        self._preview_preparing = False
        self._preview_duration = 0.0
        self.player.stop()
        self._reset_preview_controls()

    # ------------------------------------------------------------------
    # Transition settings

    def _refresh_transition(self) -> None:
        if not hasattr(self, "transition_name_label"):
            return
        self._loading_ui = True
        try:
            if not self.project:
                self.transition_name_label.configure(
                    text="未选择转场音频", fg=theme.MUTED
                )
                self.transition_meta_label.configure(text="打开项目后可设置默认转场")
                self.transition_source_label.configure(text="")
                self._set_transition_enabled(False)
                self.transition_choose_btn.set_enabled(False)
                return
            transition = self.project.transition
            has_path = bool(transition.path)
            if transition.path:
                if transition.path.is_file():
                    self.transition_name_label.configure(
                        text=self._elide(transition.path.name, 31),
                        fg=theme.TEXT_SOFT,
                    )
                    duration_text = (
                        format_duration(transition.source_duration, precise=True)
                        if transition.source_duration > 0
                        else "读取中…"
                    )
                    self.transition_meta_label.configure(
                        text=f"文件时长 {duration_text} · 插入到每两首歌之间",
                        fg=theme.MUTED,
                    )
                    self._transition_tooltip.update_text(str(transition.path))
                else:
                    self.transition_name_label.configure(
                        text=self._elide(transition.path.name, 31), fg=theme.RED
                    )
                    self.transition_meta_label.configure(
                        text="文件不存在，请重新选择", fg=theme.RED
                    )
            else:
                self.transition_name_label.configure(
                    text="未选择转场音频", fg=theme.MUTED
                )
                self.transition_meta_label.configure(
                    text="选择音频后，会在歌曲衔接处自动插入", fg=theme.MUTED
                )
                self._transition_tooltip.update_text("尚未选择转场音频")
            self.insert_transition_var.set(transition.insert_between)
            self.full_var.set(transition.full)
            self.at_end_var.set(transition.at_end)
            self.transition_duration_var.set(f"{transition.length:g}")
            self.transition_fade_var.set(f"{transition.fade:g}")
            self.transition_volume_var.set(transition.volume * 100)
            self.transition_volume_label.configure(text=f"{round(transition.volume * 100)}%")
            if transition.source_duration > 0:
                self.transition_source_label.configure(
                    text=f"原长 {format_duration(transition.source_duration)}"
                )
            else:
                self.transition_source_label.configure(text="")
            self._set_transition_enabled(has_path)
            self.transition_duration_entry.configure(
                state="disabled" if (not has_path or transition.full) else "normal"
            )
            self.transition_preview_btn.set_enabled(
                bool(has_path and transition.path and transition.path.is_file())
            )
            self._transition_tooltip.update_text(
                str(transition.path) if transition.path else "尚未选择转场音频"
            )
            self.transition_choose_btn.set_enabled(True)
        finally:
            self._loading_ui = False

    def _set_transition_enabled(self, enabled: bool) -> None:
        for widget in getattr(self, "_transition_widgets", []):
            try:
                if widget is self.transition_volume_scale:
                    widget.configure(state="normal" if enabled else "disabled")
                elif isinstance(widget, ttk.Checkbutton):
                    widget.configure(state="normal" if enabled else "disabled")
                else:
                    widget.configure(state="normal" if enabled else "disabled")
            except tk.TclError:
                pass
        for widget in (self.transition_choose_btn,):
            widget.set_enabled(True)
        if not enabled:
            self.transition_preview_btn.set_enabled(False)

    def _choose_transition(self) -> None:
        if not self.project:
            self._open_folder_dialog()
            if not self.project:
                return
        filename = filedialog.askopenfilename(
            title="选择默认转场音频",
            parent=self,
            filetypes=[
                ("音频文件", " ".join(f"*{ext}" for ext in sorted(AUDIO_EXTENSIONS))),
                ("所有文件", "*.*"),
            ],
        )
        if not filename:
            return
        path = Path(filename).resolve()
        transition = self.project.transition  # type: ignore[union-attr]
        transition.path = path
        transition.source_duration = 0.0
        self.project.dirty = True  # type: ignore[union-attr]
        removed = self._remove_transition_from_playlist(path)
        self._refresh_playlist(select=self.current_track)
        self._refresh_transition()
        self._refresh_summary()
        self._start_transition_probe(path)
        self._schedule_save()
        self._record_history("选择转场音频")
        if removed:
            self._set_status("转场已设置", f"{path.name} 已从歌曲列表移出", "busy")
        else:
            self._set_status("正在读取转场", path.name, "busy")

    def _remove_transition_from_playlist(self, path: Path) -> bool:
        if not self.project:
            return False
        target = self._path_key(path)
        matches = [track for track in self.project.tracks if self._path_key(track.path) == target]
        if not matches:
            return False
        for track in matches:
            self.project.tracks.remove(track)
            if track is self.current_track:
                self.current_track = None
        self.project.dirty = True
        if self.current_track is None:
            remaining = self.project.tracks
            self.current_track = remaining[0] if remaining else None
            if self.current_track:
                self._refresh_editor()
                self._load_waveform(self.current_track)
            else:
                self._refresh_editor()
        return True

    def _start_transition_probe(self, path: Path) -> None:
        def worker() -> None:
            try:
                info = probe_audio(path)
            except Exception as exc:
                self.events.put(("transition_probe_error", (path, str(exc))))
            else:
                self.events.put(("transition_probe_done", (path, info)))

        threading.Thread(target=worker, name="transition-probe", daemon=True).start()

    def _on_transition_setting(self) -> None:
        if self._loading_ui or not self.project:
            return
        transition = self.project.transition
        transition.insert_between = bool(self.insert_transition_var.get())
        transition.full = bool(self.full_var.get())
        transition.at_end = bool(self.at_end_var.get())
        self.project.dirty = True
        self._refresh_transition()
        self._refresh_summary()
        self._schedule_save()
        self._record_history("修改转场设置")

    def _on_transition_volume(self, value: str | float) -> None:
        if not hasattr(self, "transition_volume_label"):
            return
        try:
            percent = float(value)
        except (TypeError, ValueError):
            return
        self.transition_volume_label.configure(text=f"{round(percent)}%")
        if self._loading_ui or not self.project:
            return
        self.project.transition.volume = max(0.0, min(1.5, percent / 100.0))
        self.project.dirty = True
        self._refresh_summary()
        self._schedule_save()

    def _on_transition_volume_release(self, _event=None) -> None:
        if self._loading_ui or not self.project:
            return
        self._record_history("调整转场音量")

    def _commit_transition_fields(self, _event=None) -> str:
        if not self.project:
            return "break"
        transition = self.project.transition
        old_length = transition.length
        old_fade = transition.fade
        transition.length = min(30.0, max(0.2, _safe_float(self.transition_duration_var.get(), old_length)))
        transition.fade = min(5.0, max(0.0, _safe_float(self.transition_fade_var.get(), old_fade)))
        transition.validate()
        self.transition_duration_var.set(f"{transition.length:g}")
        self.transition_fade_var.set(f"{transition.fade:g}")
        if (old_length, old_fade) != (transition.length, transition.fade):
            self.project.dirty = True
            self._refresh_summary()
            self._schedule_save()
            self._record_history("修改转场参数")
        return "break"

    # ------------------------------------------------------------------
    # Output settings

    def _refresh_output(self) -> None:
        if not hasattr(self, "format_combo"):
            return
        self._loading_ui = True
        try:
            if not self.project:
                self.format_var.set("MP3")
                self.bitrate_var.set("320k")
                self.filename_var.set("随舞混剪.mp3")
                self.format_combo.configure(state="disabled")
                self.bitrate_combo.configure(state="disabled")
                self.filename_entry.configure(state="normal", fg=theme.MUTED)
                self.filename_entry.configure(state="readonly")
                self.normalize_var.set(False)
                self.normalize_check.configure(state="disabled")
                self.export_duration_label.configure(text="—")
                self.export_detail_label.configure(text="0 段 · 0 次转场")
                self.open_output_btn.set_enabled(False)
                return
            output = self.project.output
            self.format_var.set(FORMAT_LABELS.get(output.fmt, "MP3"))
            self.bitrate_var.set(output.bitrate if output.bitrate in BITRATE_CHOICES else "320k")
            self.filename_var.set(output.filename or f"随舞混剪.{output.fmt}")
            self.format_combo.configure(state="readonly")
            self.filename_entry.configure(state="normal", fg=theme.TEXT)
            self.normalize_var.set(bool(output.normalize))
            self.normalize_check.configure(state="normal")
            self._update_bitrate_state()
        finally:
            self._loading_ui = False

    def _update_bitrate_state(self) -> None:
        if not self.project:
            self.bitrate_combo.configure(state="disabled")
            return
        if self.project.output.fmt in {"wav", "flac"}:
            self.bitrate_combo.configure(state="disabled")
        else:
            self.bitrate_combo.configure(state="readonly")

    def _on_format_change(self, _event=None) -> None:
        if self._loading_ui or not self.project:
            return
        choice = self.format_var.get()
        fmt = FORMAT_CHOICES.get(choice, "mp3")
        self.project.output.fmt = fmt
        self.project.update_output_extension()
        self.filename_var.set(self.project.output.filename)
        self.project.dirty = True
        self._update_bitrate_state()
        self._refresh_summary()
        self._schedule_save()
        self._record_history("修改导出格式")

    def _on_bitrate_change(self, _event=None) -> None:
        if self._loading_ui or not self.project:
            return
        self.project.output.bitrate = self.bitrate_var.get()
        self.project.dirty = True
        self._schedule_save()
        self._record_history("修改导出码率")

    def _on_normalize_change(self) -> None:
        if self._loading_ui or not self.project:
            return
        self.project.output.normalize = bool(self.normalize_var.get())
        self.project.dirty = True
        self._refresh_summary()
        self._schedule_save()
        self._record_history("切换统一响度")

    def _commit_filename(self, _event=None) -> str:
        if self._loading_ui or not self.project:
            return "break"
        raw = self.filename_var.get().strip()
        stem = Path(raw).stem if raw else "随舞混剪"
        stem = _sanitize_filename(stem)
        current_fmt = self.project.output.fmt
        filename = f"{stem}.{current_fmt}"
        changed = filename != self.project.output.filename
        self.project.output.filename = filename
        self.filename_var.set(filename)
        if changed:
            self.project.dirty = True
            self._schedule_save()
            self._record_history("修改导出文件名")
        return "break"

    def _refresh_summary(self) -> None:
        if not hasattr(self, "export_duration_label"):
            return
        if not self.project:
            self.export_duration_label.configure(text="—")
            self.export_detail_label.configure(text="0 段 · 0 次转场")
            return
        tracks = self.project.enabled_tracks()
        total = self.project.total_duration()
        transitions = self.project.transition_boundary_count()
        self.export_duration_label.configure(
            text=format_duration_zh(total) if tracks else "—"
        )
        detail = f"{len(tracks)} 段音频"
        detail += f" · {transitions} 次转场" if transitions else " · 无转场"
        self.export_detail_label.configure(text=detail)
        missing = [track for track in tracks if track.is_missing]
        unread = [track for track in tracks if track.duration <= 0]
        can_export = bool(tracks) and not missing and not unread and not self._export_running
        if not self._export_running:
            self.export_btn.set_enabled(can_export)
        self._refresh_counts()

    # ------------------------------------------------------------------
    # Saving / status / poller

    def _schedule_save(self, delay: int = 650) -> None:
        if self._save_after_id:
            try:
                self.after_cancel(self._save_after_id)
            except tk.TclError:
                pass
        self._save_after_id = self.after(delay, self._save_now)

    def _save_now(self, announce: bool = False) -> None:
        self._save_after_id = None
        if not self.project:
            return
        try:
            self.project.save()
        except Exception as exc:
            self._set_status("保存失败", str(exc), "error")
            return
        if announce:
            self._set_status("项目已保存", str(self.project.folder / "随舞项目.json"), "success")

    def _poll_events(self) -> None:
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._dispatch_event(kind, payload)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        if not self._closing:
            self._events_after_id = self.after(50, self._poll_events)

    def _dispatch_event(self, kind: str, payload) -> None:
        if kind == "probe_done":
            self._handle_probe_done(payload)
        elif kind == "probe_error":
            self._handle_probe_error(payload)
        elif kind == "probe_finished":
            self._probing = False
            self._start_probe_worker()
            self._save_now()
            if self.project and not any(
                track.duration <= 0 and not track.probe_error for track in self.project.tracks
            ):
                self._set_status("准备就绪", "歌曲信息已读取完成", "success")
        elif kind == "wave_done":
            self._handle_wave_done(payload)
        elif kind == "wave_error":
            self._handle_wave_error(payload)
        elif kind == "transition_probe_done":
            self._handle_transition_probe_done(payload)
        elif kind == "transition_probe_error":
            self._handle_transition_probe_error(payload)
        elif kind == "preview_ready":
            self._handle_preview_ready(payload)
        elif kind == "preview_error":
            self._handle_preview_error(payload)
        elif kind == "export_progress":
            self._handle_export_progress(payload)
        elif kind == "export_done":
            self._handle_export_done(payload)
        elif kind == "export_error":
            self._handle_export_error(payload)
        elif kind == "export_cancelled":
            self._handle_export_cancelled(payload)

    def _handle_probe_done(self, payload) -> None:
        track, info = payload
        if not self.project or track not in self.project.tracks:
            return
        track.duration = max(0.0, info.duration)
        track.codec = info.codec
        track.sample_rate = info.sample_rate
        track.channels = info.channels
        track.probe_error = ""
        if track.end <= 0:
            track.start = 0.0
            track.end = track.duration
        else:
            track.normalise()
        self._update_tree_row(track)
        if track is self.current_track:
            self._refresh_editor()
            self._load_waveform(track)
        self._refresh_summary()
        self._schedule_save()
        self._sync_history_baseline()

    def _handle_probe_error(self, payload) -> None:
        track, error = payload
        if not self.project or track not in self.project.tracks:
            return
        track.probe_error = error
        self._update_tree_row(track)
        if track is self.current_track:
            self._refresh_editor()
        self._set_status("歌曲读取失败", f"{track.name}：{error}", "error")
        self._schedule_save()
        self._sync_history_baseline()

    def _handle_transition_probe_done(self, payload) -> None:
        path, info = payload
        if not self.project or self.project.transition.path != path:
            return
        self.project.transition.source_duration = max(0.0, info.duration)
        self.project.transition.validate()
        self.project.dirty = True
        self._refresh_transition()
        self._refresh_summary()
        self._schedule_save()
        self._sync_history_baseline()
        self._set_status("转场已设置", f"{path.name} · {format_duration(info.duration)}", "success")

    def _handle_transition_probe_error(self, payload) -> None:
        path, error = payload
        if not self.project or self.project.transition.path != path:
            return
        self.project.transition.source_duration = 0.0
        self.project.transition.path = None
        self.project.dirty = True
        self._refresh_transition()
        self._refresh_summary()
        self._schedule_save()
        self._sync_history_baseline()
        messagebox.showerror("转场音频不可用", error, parent=self)
        self._set_status("转场读取失败", error, "error")

    # ------------------------------------------------------------------
    # Status / export

    def _set_status(self, text: str, detail: str = "", kind: str = "info") -> None:
        if not hasattr(self, "status_label"):
            return
        colors = {
            "info": theme.ACCENT,
            "success": theme.MINT,
            "error": theme.RED,
            "warn": theme.ORANGE,
            "busy": theme.ACCENT_HOVER,
        }
        self.status_label.configure(text=text)
        self.status_dot.itemconfigure(self.status_dot_id, fill=colors.get(kind, theme.ACCENT))
        if detail:
            self.footer_detail.configure(text=detail)

    def _start_export(self) -> None:
        if not self.project:
            self._open_folder_dialog()
            return
        if self._export_running:
            return
        self._commit_transition_fields()
        self._commit_filename()
        selected = self.project.enabled_tracks()
        if not selected:
            messagebox.showwarning("没有可导出的歌曲", "请至少启用一首歌曲。", parent=self)
            return
        missing = [track for track in selected if track.is_missing]
        if missing:
            messagebox.showerror(
                "音频文件缺失",
                "以下歌曲文件不存在，无法导出：\n\n" + "\n".join(track.name for track in missing[:6]),
                parent=self,
            )
            return
        unread = [track for track in selected if track.duration <= 0]
        if unread:
            messagebox.showinfo(
                "请稍候",
                "还有歌曲的时长没有读取完成，请等待分析结束后再导出。",
                parent=self,
            )
            return
        if any(track.clip_duration <= 0.05 for track in selected):
            messagebox.showwarning("选段无效", "有歌曲的剪辑段太短，请重新选择。", parent=self)
            return
        transition = self.project.transition
        if transition.path and (transition.insert_between or transition.at_end):
            if not transition.path.is_file():
                messagebox.showerror("转场音频缺失", "默认转场文件不存在，请重新选择。", parent=self)
                return
            if transition.source_duration <= 0:
                messagebox.showinfo("请稍候", "正在读取转场音频，请稍后再导出。", parent=self)
                return

        clips = [
            ClipSpec(
                path=track.path,
                start=track.start,
                end=track.end,
                name=track.name,
            )
            for track in selected
        ]
        transition_spec = None
        if transition.path:
            transition_spec = TransitionSpec(
                path=transition.path,
                source_duration=transition.source_duration,
                length=transition.length,
                full=transition.full,
                volume=transition.volume,
                fade=transition.fade,
                insert_between=transition.insert_between,
                at_end=transition.at_end,
            )
        output_path = self.project.output_path()
        try:
            plan = build_export_plan(
                clips,
                transition_spec,
                output_path,
                fmt=self.project.output.fmt,
                bitrate=self.project.output.bitrate,
                title="随舞混剪",
                normalize=self.project.output.normalize,
            )
        except AudioError as exc:
            messagebox.showerror("无法导出", str(exc), parent=self)
            return
        plan.output_path = make_unique_path(plan.output_path)
        if plan.output_path != output_path:
            self.project.output.filename = plan.output_path.name
            self.filename_var.set(plan.output_path.name)
        self.project.dirty = True
        self._save_now()
        self._sync_history_baseline()
        self._export_running = True
        self._export_cancel = threading.Event()
        self.cancel_btn.set_enabled(True)
        self._export_path = plan.output_path
        self.footer_progress.set_progress(0.0)
        self.export_btn.set_text("正在导出…")
        self.export_btn.set_enabled(False)
        self.cancel_btn.grid()
        self._set_status(
            "正在导出混剪",
            f"{len(clips)} 段音频 · 预计 {format_duration_zh(plan.total_duration)}",
            "busy",
        )
        self._refresh_action_states()

        cancel_event = self._export_cancel

        def worker() -> None:
            try:
                result = run_export(
                    plan,
                    progress=lambda value: self.events.put(("export_progress", value)),
                    cancel_event=cancel_event,
                )
            except AudioCancelled as exc:
                self.events.put(("export_cancelled", str(exc)))
            except Exception as exc:
                self.events.put(("export_error", str(exc)))
            else:
                self.events.put(("export_done", (result, plan.total_duration)))

        threading.Thread(target=worker, name="export", daemon=True).start()

    def _handle_export_progress(self, value: float) -> None:
        if not self._export_running:
            return
        self.footer_progress.set_progress(value)
        percent = round(value * 100)
        self._set_status(
            "正在导出混剪",
            f"{percent}% · 正在编码音频…",
            "busy",
        )

    def _handle_export_done(self, payload) -> None:
        path, duration = payload
        self._export_running = False
        self._export_cancel = None
        self._export_path = None
        self._last_export = Path(path)
        self.cancel_btn.set_enabled(True)
        self.cancel_btn.grid_remove()
        self.footer_progress.set_progress(1.0)
        self.export_btn.set_enabled(True)
        self.export_btn.set_text("导出混剪")
        self._refresh_action_states()
        self._set_status(
            "导出完成",
            f"{Path(path).name} · {format_duration_zh(duration)}",
            "success",
        )
        answer = messagebox.askyesno(
            "导出完成",
            f"已生成混剪文件：\n\n{path}\n\n时长：{format_duration_zh(duration)}\n\n是否打开输出文件夹？",
            parent=self,
        )
        if answer:
            self._open_output_folder()
        self.after(1600, lambda: self.footer_progress.set_progress(0.0))

    def _handle_export_error(self, error: str) -> None:
        self._export_running = False
        self._export_cancel = None
        self._export_path = None
        self.cancel_btn.set_enabled(True)
        self.cancel_btn.grid_remove()
        self.export_btn.set_enabled(True)
        self.export_btn.set_text("导出混剪")
        self._refresh_action_states()
        self.footer_progress.set_progress(0.0)
        self._set_status("导出失败", error, "error")
        messagebox.showerror("导出失败", error, parent=self)

    def _handle_export_cancelled(self, message: str) -> None:
        self._export_running = False
        self._export_cancel = None
        self._export_path = None
        self.cancel_btn.set_enabled(True)
        self.cancel_btn.grid_remove()
        self.export_btn.set_enabled(True)
        self.export_btn.set_text("导出混剪")
        self._refresh_action_states()
        self.footer_progress.set_progress(0.0)
        self._set_status("已取消导出", message, "warn")

    def _cancel_export(self) -> None:
        if self._export_cancel is not None:
            self._export_cancel.set()
            self.cancel_btn.set_enabled(False)
            self._set_status("正在取消导出", "等待 FFmpeg 结束当前任务…", "warn")

    def _open_output_folder(self) -> None:
        if not self.project:
            return
        folder = self.project.folder / OUTPUT_FOLDER
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("无法打开输出目录", str(exc), parent=self)

    def _reveal_track(self, track: Track) -> None:
        if not track.path.exists():
            messagebox.showerror("文件不存在", str(track.path), parent=self)
            return
        try:
            subprocess.Popen(
                ["explorer", "/select,", str(track.path)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            try:
                os.startfile(str(track.path.parent))  # type: ignore[attr-defined]
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Closing / error handling

    def _on_close(self) -> None:
        if self._closing:
            return
        if self._export_running:
            if not messagebox.askyesno(
                "正在导出",
                "导出尚未完成，确定要退出吗？\n当前导出将被取消。",
                parent=self,
            ):
                return
            if self._export_cancel:
                self._export_cancel.set()
        self._closing = True
        self._stop_preview()
        self._save_now()
        self._save_settings()
        if self._events_after_id:
            try:
                self.after_cancel(self._events_after_id)
            except tk.TclError:
                pass
        if self._save_after_id:
            try:
                self.after_cancel(self._save_after_id)
            except tk.TclError:
                pass
        self.player.stop()
        try:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        except OSError:
            pass
        self.destroy()

    def report_callback_exception(self, exc, value, tb) -> None:
        traceback.print_exception(exc, value, tb)
        if self._closing:
            return
        message = "".join(traceback.format_exception_only(exc, value)).strip()
        try:
            self._set_status("程序遇到错误", message, "error")
        except tk.TclError:
            pass
        messagebox.showerror("程序遇到错误", message, parent=self)


def main() -> None:
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "SuiBuCut.Desktop.1"
            )
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = SuibuApp()
    app.mainloop()
