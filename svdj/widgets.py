"""Custom Tk widgets used by the 随舞剪辑台 interface."""

from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont
from typing import Callable

from . import theme
from .audio import format_duration


def _blend(color_a: str, color_b: str, amount: float) -> str:
    amount = max(0.0, min(1.0, amount))
    a = tuple(int(color_a[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(color_b[i : i + 2], 16) for i in (1, 3, 5))
    mixed = tuple(round(x + (y - x) * amount) for x, y in zip(a, b))
    return "#" + "".join(f"{value:02x}" for value in mixed)


def rounded_rectangle(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    **kwargs,
) -> int:
    radius = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=18, **kwargs)


class Card(tk.Frame):
    def __init__(self, master, padding: int = 18, bg: str = theme.SURFACE, **kwargs):
        super().__init__(
            master,
            bg=bg,
            highlightbackground=theme.BORDER,
            highlightcolor=theme.BORDER,
            highlightthickness=1,
            bd=0,
            **kwargs,
        )
        self.body = tk.Frame(self, bg=bg, bd=0, highlightthickness=0)
        self.body.pack(fill="both", expand=True, padx=padding, pady=padding)
        self.card_bg = bg


class RoundedButton(tk.Canvas):
    """A compact, anti-aliased-looking button drawn on a canvas."""

    STYLES = {
        "primary": {
            "bg": theme.ACCENT,
            "bg_hover": theme.ACCENT_HOVER,
            "bg_press": theme.ACCENT_PRESS,
            "fg": theme.WHITE,
            "border": theme.ACCENT,
            "border_hover": theme.ACCENT_HOVER,
        },
        "secondary": {
            "bg": theme.SURFACE_HOVER,
            "bg_hover": "#2E3747",
            "bg_press": "#1F2633",
            "fg": theme.TEXT,
            "border": theme.BORDER,
            "border_hover": "#3B4558",
        },
        "ghost": {
            "bg": theme.SURFACE,
            "bg_hover": theme.SURFACE_HOVER,
            "bg_press": theme.SURFACE_ALT,
            "fg": theme.TEXT_SOFT,
            "border": theme.BORDER,
            "border_hover": "#343D4D",
        },
        "quiet": {
            "bg": theme.SURFACE,
            "bg_hover": theme.SURFACE_ALT,
            "bg_press": theme.SURFACE_ALT,
            "fg": theme.MUTED,
            "border": theme.SURFACE,
            "border_hover": theme.SURFACE_ALT,
        },
        "danger": {
            "bg": theme.SURFACE,
            "bg_hover": theme.RED_DEEP,
            "bg_press": theme.RED_DEEP,
            "fg": theme.RED,
            "border": theme.BORDER,
            "border_hover": theme.RED_DEEP,
        },
        "mint": {
            "bg": theme.MINT,
            "bg_hover": "#5FE3B5",
            "bg_press": "#32C390",
            "fg": "#06231A",
            "border": theme.MINT,
            "border_hover": "#5FE3B5",
        },
    }

    def __init__(
        self,
        master,
        text: str,
        command: Callable[[], None] | None = None,
        style: str = "secondary",
        width: int | None = None,
        height: int = 36,
        font_size: int = 9,
        bold: bool = True,
        radius: int = 9,
        background: str | None = None,
        padx: int = 16,
    ):
        self._parent_bg = background or master.cget("bg")
        super().__init__(
            master,
            bg=self._parent_bg,
            highlightthickness=0,
            bd=0,
            height=height,
            takefocus=1,
            cursor="hand2",
        )
        self._text = text
        self._command = command
        self._style_name = style
        self._height = height
        self._radius = radius
        self._font = tkfont.Font(family=theme.Fonts.ui, size=font_size, weight="bold" if bold else "normal")
        self._padding = padx
        measured = self._font.measure(text) + padx * 2
        self._width = width or max(measured, 48)
        self.configure(width=self._width, height=height)
        self._state = "normal"
        self._hover = False
        self._pressed = False
        self._shape: int | None = None
        self._label: int | None = None
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", self._activate)
        self.bind("<space>", self._activate)
        self.bind("<FocusIn>", lambda _event: self._draw())
        self.bind("<FocusOut>", lambda _event: self._draw())

    def _palette(self) -> dict:
        base = self.STYLES.get(self._style_name, self.STYLES["secondary"])
        if self._state == "disabled":
            return {
                "bg": theme.SURFACE_ALT,
                "bg_hover": theme.SURFACE_ALT,
                "bg_press": theme.SURFACE_ALT,
                "fg": theme.MUTED_DARK,
                "border": theme.BORDER_SOFT,
                "border_hover": theme.BORDER_SOFT,
            }
        return base

    def _draw(self) -> None:
        self.delete("all")
        palette = self._palette()
        fill = palette["bg"]
        outline = palette["border"]
        if self._pressed and self._state != "disabled":
            fill = palette["bg_press"]
        elif self._hover and self._state != "disabled":
            fill = palette["bg_hover"]
            outline = palette["border_hover"]
        border_width = 1
        if self.focus_get() is self and self._state != "disabled":
            outline = _blend(outline, theme.ACCENT, 0.7)
        self._shape = rounded_rectangle(
            self,
            1,
            1,
            self._width - 1,
            self._height - 1,
            self._radius,
            fill=fill,
            outline=outline,
            width=border_width,
        )
        self._label = self.create_text(
            self._width / 2,
            self._height / 2 + (0 if self._state != "disabled" else 0),
            text=self._text,
            fill=palette["fg"],
            font=self._font,
        )

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_press(self, _event=None) -> None:
        if self._state == "disabled":
            return
        self.focus_set()
        self._pressed = True
        self._draw()

    def _on_release(self, event=None) -> None:
        if self._state == "disabled":
            return
        was_pressed = self._pressed
        self._pressed = False
        self._draw()
        if (
            was_pressed
            and event is not None
            and 0 <= event.x <= self._width
            and 0 <= event.y <= self._height
            and self._command
        ):
            self._command()

    def _activate(self, _event=None) -> None:
        if self._state != "disabled" and self._command:
            self._command()
        return "break"

    def set_enabled(self, enabled: bool) -> None:
        self._state = "normal" if enabled else "disabled"
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw()

    def set_style(self, style: str) -> None:
        self._style_name = style
        self._draw()

    def button_config(self, **kwargs) -> None:
        """Small compatibility helper for code that wants to recolor a button."""
        if "state" in kwargs:
            self.set_enabled(kwargs.pop("state") not in {"disabled", "disable"})
        if "text" in kwargs:
            self._text = kwargs.pop("text")
        if kwargs:
            super().configure(**kwargs)
        self._draw()


class WaveformView(tk.Canvas):
    MIN_LENGTH = 0.15

    MIN_VIEW_SECONDS = 0.2

    def __init__(
        self,
        master,
        on_change: Callable[[float, float, bool], None] | None = None,
        on_view_change: Callable[[float, float, float], None] | None = None,
        on_audition: Callable[[float | None], None] | None = None,
        on_context: Callable[[float, int, int], None] | None = None,
    ):
        super().__init__(
            master,
            bg=theme.INPUT,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            highlightcolor=theme.BORDER,
            bd=0,
            height=196,
            cursor="crosshair",
        )
        self._on_change = on_change
        self._on_view_change = on_view_change
        self._on_audition = on_audition
        self._on_context = on_context
        self._peaks: tuple[float, ...] = ()
        self._duration = 0.0
        self._start = 0.0
        self._end = 0.0
        self._view_start = 0.0
        self._view_end = 0.0
        self._audition: float | None = None
        self._loading_text = "选择歌曲后显示波形"
        self._drag_mode: str | None = None
        self._drag_offset = 0.0
        self._playhead: float | None = None
        self._needs_redraw = False
        self._pan_x: float | None = None
        self._pan_view_start = 0.0
        self.bind("<Configure>", self._on_resize)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<Shift-ButtonPress-1>", self._on_shift_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Motion>", self._on_motion)
        self.bind("<MouseWheel>", self._on_mousewheel)
        self.bind("<Button-3>", self._on_context_click)
        self.bind("<ButtonPress-2>", self._on_pan_start)
        self.bind("<B2-Motion>", self._on_pan_motion)
        self.bind("<ButtonRelease-2>", self._on_pan_end)
        self.bind("<Leave>", lambda _e: self.configure(cursor="crosshair"))
        self.after(20, self.redraw)

    # ------------------------------------------------------------------
    # Public API

    def set_loading(self, text: str = "正在分析波形…") -> None:
        self._peaks = ()
        self._duration = 0.0
        self._view_start = 0.0
        self._view_end = 0.0
        self._audition = None
        self._loading_text = text
        self.redraw()
        self._notify_view()
        self._notify_audition()

    def set_data(
        self,
        peaks: tuple[float, ...],
        duration: float,
        start: float,
        end: float,
        reset_view: bool = False,
    ) -> None:
        self._peaks = peaks
        self._duration = max(0.01, duration)
        self._start = max(0.0, min(start, self._duration))
        self._end = max(self._start + self.MIN_LENGTH, min(end, self._duration))
        self._playhead = None
        view_valid = self._view_end > self._view_start
        if reset_view or not view_valid:
            self._fit_view_internal(notify=False)
        else:
            self._clamp_view()
        if reset_view:
            self._set_audition_internal(None, notify=False)
        self.redraw()
        self._notify_view()

    def clear(self, text: str = "选择歌曲后显示波形") -> None:
        self._peaks = ()
        self._duration = 0.0
        self._view_start = 0.0
        self._view_end = 0.0
        self._audition = None
        self._loading_text = text
        self._playhead = None
        self.redraw()
        self._notify_view()
        self._notify_audition()

    def set_selection(self, start: float, end: float, notify: bool = False) -> None:
        if self._duration <= 0:
            return
        self._start = max(0.0, min(start, self._duration - self.MIN_LENGTH))
        self._end = max(self._start + self.MIN_LENGTH, min(end, self._duration))
        self._clamp_audition_to_selection()
        self.redraw()
        if notify and self._on_change:
            self._on_change(self._start, self._end, True)

    # ------------------------------------------------------------------
    # View / zoom / audition API

    @property
    def duration(self) -> float:
        return self._duration

    def view_range(self) -> tuple[float, float]:
        return self._view_start, self._view_end

    def is_zoomed(self) -> bool:
        return (
            self._duration > 0
            and self._view_end - self._view_start < self._duration - 0.001
        )

    def set_view(self, start: float, end: float, notify: bool = True) -> None:
        if self._duration <= 0:
            return
        length = max(self.MIN_VIEW_SECONDS, min(self._duration, end - start))
        start = max(0.0, min(start, self._duration - length))
        self._view_start = start
        self._view_end = start + length
        self.redraw()
        if notify:
            self._notify_view()

    def fit_view(self, notify: bool = True) -> None:
        if self._duration <= 0:
            return
        self._fit_view_internal(notify=notify)
        self.redraw()

    def zoom(self, factor: float, center_x: float | None = None) -> None:
        if self._duration <= 0 or factor <= 0:
            return
        current_length = max(self.MIN_VIEW_SECONDS, self._view_end - self._view_start)
        min_length = self._minimum_view_length()
        target_length = max(min_length, min(self._duration, current_length / factor))
        if abs(target_length - current_length) < 1e-6:
            return
        left, right, _top, _bottom, _axis = self._geometry()
        if center_x is None:
            ratio = 0.5
        else:
            ratio = max(0.0, min(1.0, (center_x - left) / max(1.0, right - left)))
        anchor = self._view_start + ratio * current_length
        start = max(0.0, min(anchor - ratio * target_length, self._duration - target_length))
        self._view_start = start
        self._view_end = start + target_length
        self.redraw()
        self._notify_view()

    def pan(self, seconds: float) -> None:
        if self._duration <= 0:
            return
        length = self._view_end - self._view_start
        if length >= self._duration - 1e-6:
            return
        start = max(0.0, min(self._view_start + seconds, self._duration - length))
        if abs(start - self._view_start) < 1e-9:
            return
        self._view_start = start
        self._view_end = start + length
        self.redraw()
        self._notify_view()

    def scroll_fraction(self, first: float) -> None:
        if self._duration <= 0:
            return
        length = self._view_end - self._view_start
        start = max(0.0, min(first * self._duration, self._duration - length))
        self.set_view(start, start + length)

    def audition_time(self) -> float | None:
        return self._audition

    def clear_audition(self, notify: bool = True) -> None:
        self._set_audition_internal(None, notify=notify)
        self.redraw()

    def set_audition(self, seconds: float | None, notify: bool = True) -> None:
        if seconds is None:
            self.clear_audition(notify=notify)
            return
        if self._duration <= 0:
            return
        value = max(self._start, min(seconds, self._end))
        self._set_audition_internal(value, notify=notify)
        self.redraw()

    def _minimum_view_length(self) -> float:
        return min(
            self._duration,
            max(self.MIN_VIEW_SECONDS, self._duration / 200.0),
        )

    def _fit_view_internal(self, notify: bool) -> None:
        self._view_start = 0.0
        self._view_end = self._duration
        if notify:
            self._notify_view()

    def _clamp_view(self) -> None:
        if self._duration <= 0:
            self._view_start = 0.0
            self._view_end = 0.0
            return
        length = max(
            self._minimum_view_length(),
            min(self._duration, self._view_end - self._view_start),
        )
        start = max(0.0, min(self._view_start, self._duration - length))
        self._view_start = start
        self._view_end = start + length

    def _set_audition_internal(self, seconds: float | None, notify: bool) -> None:
        if self._audition == seconds:
            return
        self._audition = seconds
        if notify:
            self._notify_audition()

    def _clamp_audition_to_selection(self) -> None:
        if self._audition is not None and not (
            self._start - 1e-9 <= self._audition <= self._end + 1e-9
        ):
            self._set_audition_internal(None, notify=True)

    def _notify_view(self) -> None:
        if self._on_view_change:
            self._on_view_change(
                self._view_start,
                self._view_end,
                self._duration,
            )

    def _notify_audition(self) -> None:
        if self._on_audition:
            self._on_audition(self._audition)

    def set_playhead(self, seconds: float | None) -> None:
        self._playhead = seconds
        self.delete("playhead")
        if seconds is None or self._duration <= 0 or not self._peaks:
            return
        left, right, _top, _bottom, axis_y = self._geometry()
        if seconds < self._view_start or seconds > self._view_end:
            return
        ratio = max(0.0, min(1.0, (seconds - self._view_start) / max(1e-9, self._view_end - self._view_start)))
        x = left + ratio * (right - left)
        self.create_line(x, 28, x, axis_y + 42, fill=theme.WHITE, width=1, tags="playhead")
        self.create_polygon(
            x - 4,
            24,
            x + 4,
            24,
            x,
            30,
            fill=theme.WHITE,
            outline="",
            tags="playhead",
        )

    # ------------------------------------------------------------------
    # Drawing

    def _geometry(self) -> tuple[float, float, float, float, float]:
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        left = 16.0
        right = width - 16.0
        top = 34.0
        bottom = height - 28.0
        axis_y = top + (bottom - top) / 2
        return left, right, top, bottom, axis_y

    def _x_for_time(self, seconds: float) -> float:
        left, right, _top, _bottom, _axis = self._geometry()
        if self._duration <= 0 or self._view_end <= self._view_start:
            return left
        return left + (
            (seconds - self._view_start) / (self._view_end - self._view_start)
        ) * (right - left)

    def _time_for_x(self, x: float) -> float:
        left, right, _top, _bottom, _axis = self._geometry()
        if right <= left:
            return self._view_start
        ratio = max(0.0, min(1.0, (x - left) / (right - left)))
        return self._view_start + ratio * (self._view_end - self._view_start)

    def _on_resize(self, _event=None) -> None:
        if not self._needs_redraw:
            self._needs_redraw = True
            self.after_idle(self._redraw_if_needed)

    def _redraw_if_needed(self) -> None:
        self._needs_redraw = False
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        left, right, top, bottom, axis_y = self._geometry()
        if width < 80 or height < 80:
            return
        if not self._peaks or self._duration <= 0:
            self.create_text(
                width / 2,
                height / 2,
                text=self._loading_text,
                fill=theme.MUTED,
                font=theme.Fonts.font(10),
            )
            return

        x_start = self._x_for_time(self._start)
        x_end = self._x_for_time(self._end)
        # Selection wash and axis. Negative / oversized coordinates are
        # clipped by Tk, but keeping the rectangle inside the chart avoids
        # the selection shading the ruler.
        wash_left = max(left, min(x_start, x_end))
        wash_right = min(right, max(x_start, x_end))
        if wash_right > wash_left:
            self.create_rectangle(
                wash_left,
                top - 4,
                wash_right,
                bottom + 4,
                fill=theme.ACCENT_SOFT,
                outline="",
            )
        self.create_line(left, axis_y, right, axis_y, fill="#242C3A", width=1)
        for quarter in (0.25, 0.5, 0.75):
            x = left + (right - left) * quarter
            self.create_line(x, top, x, bottom, fill="#1B222D", dash=(1, 5))

        step = 3
        count = max(1, int((right - left) / step))
        max_amp = (bottom - top) / 2
        view_length = max(1e-6, self._view_end - self._view_start)
        for index in range(count):
            x = left + index * step
            ratio = index / max(1, count - 1)
            time_ratio = (self._view_start + ratio * view_length) / self._duration
            peak_index = max(0, min(len(self._peaks) - 1, int(time_ratio * len(self._peaks))))
            peak = self._peaks[peak_index]
            selected = x_start <= x <= x_end
            if selected:
                color = _blend("#6248C9", "#B59EFF", peak)
            else:
                color = _blend("#2D3544", "#4A5468", peak)
            height_px = max(2.0, peak * max_amp * 0.94)
            if peak < 0.015:
                height_px = 1.5
            self.create_line(
                x,
                axis_y - height_px,
                x,
                axis_y + height_px,
                fill=color,
                width=2,
                capstyle=tk.ROUND,
            )

        # Boundary handles and labels are only drawn when their x coordinate
        # is inside the visible viewport.
        start_visible = left - 1 <= x_start <= right + 1
        end_visible = left - 1 <= x_end <= right + 1
        start_text = f"入 {format_duration(self._start, precise=True)}"
        end_text = f"出 {format_duration(self._end, precise=True)}"
        tag_font = theme.Fonts.font(8, "bold", family=theme.Fonts.mono)
        start_width = tkfont.Font(font=tag_font).measure(start_text) + 12
        end_width = tkfont.Font(font=tag_font).measure(end_text) + 12
        end_tag_y = 9
        if (
            start_visible
            and end_visible
            and (x_end - x_start) < (start_width + end_width + 8)
        ):
            # Narrow clip: put the outgoing tag on a second row so the two
            # labels do not cover each other.
            end_tag_y = 31
        if start_visible:
            self.create_line(x_start, top - 7, x_start, bottom + 7, fill=theme.MINT, width=2)
            self.create_rectangle(
                x_start - 3,
                axis_y - 15,
                x_start + 3,
                axis_y + 15,
                fill=theme.MINT,
                outline="",
            )
            self._draw_tag(
                x_start,
                9,
                start_text,
                theme.MINT,
                theme.MINT_DEEP,
                anchor="nw",
            )
        if end_visible:
            self.create_line(x_end, top - 7, x_end, bottom + 7, fill=theme.ORANGE, width=2)
            self.create_rectangle(
                x_end - 3,
                axis_y - 15,
                x_end + 3,
                axis_y + 15,
                fill=theme.ORANGE,
                outline="",
            )
            self._draw_tag(
                x_end,
                end_tag_y,
                end_text,
                theme.ORANGE,
                theme.ORANGE_DEEP,
                anchor="ne",
            )

        # Audition marker: sky blue dashed guide, distinct from green / orange
        # clip boundaries and the white playback cursor.
        if self._audition is not None:
            x_audition = self._x_for_time(self._audition)
            if left - 1 <= x_audition <= right + 1:
                self.create_line(
                    x_audition,
                    top - 9,
                    x_audition,
                    bottom + 7,
                    fill=theme.AUDITION,
                    width=1,
                    dash=(3, 3),
                )
                self.create_polygon(
                    x_audition - 4,
                    top - 11,
                    x_audition + 4,
                    top - 11,
                    x_audition,
                    top - 4,
                    fill=theme.AUDITION,
                    outline="",
                )

        # Ruler uses the visible window, not the full track.
        tick_font = theme.Fonts.font(8, family=theme.Fonts.mono)
        precise_ticks = view_length < 20
        for index in range(5):
            ratio = index / 4
            x = left + (right - left) * ratio
            seconds = self._view_start + view_length * ratio
            anchor = "n" if index == 2 else ("nw" if index < 2 else "ne")
            self.create_text(
                x,
                height - 12,
                text=format_duration(seconds, precise=precise_ticks),
                fill=theme.MUTED_DARK,
                font=tick_font,
                anchor=anchor,
            )

        if self._playhead is not None:
            self.set_playhead(self._playhead)

    def _draw_tag(
        self,
        x: float,
        y: float,
        text: str,
        foreground: str,
        background: str,
        anchor: str,
    ) -> None:
        font = theme.Fonts.font(8, "bold", family=theme.Fonts.mono)
        width = tkfont.Font(font=font).measure(text) + 12
        if anchor == "ne":
            x1, x2 = x - width, x
        else:
            x1, x2 = x, x + width
        # Keep labels inside the canvas when a handle is at the edge.
        if x2 > self.winfo_width() - 8:
            shift = x2 - (self.winfo_width() - 8)
            x1 -= shift
            x2 -= shift
        if x1 < 8:
            shift = 8 - x1
            x1 += shift
            x2 += shift
        rounded_rectangle(self, x1, y, x2, y + 19, 5, fill=background, outline="")
        self.create_text(
            (x1 + x2) / 2,
            y + 10,
            text=text,
            fill=foreground,
            font=font,
            anchor="center",
        )

    # ------------------------------------------------------------------
    # Interaction

    def _on_press(self, event) -> None:
        if not self._peaks or self._duration <= 0:
            return
        left, right, top, bottom, _axis = self._geometry()
        if not (left - 5 <= event.x <= right + 5):
            return
        x_start = self._x_for_time(self._start)
        x_end = self._x_for_time(self._end)
        time = self._time_for_x(event.x)
        self.focus_set()
        # The bottom ruler is the audition-position strip. It is separate
        # from the clip handles so clicking there never edits the segment.
        if event.y >= self.winfo_height() - 25 and not (
            abs(event.x - x_start) <= 8 or abs(event.x - x_end) <= 8
        ):
            self._try_set_audition(time)
            return
        if abs(event.x - x_start) <= 8:
            self._drag_mode = "start"
        elif abs(event.x - x_end) <= 8:
            self._drag_mode = "end"
        elif x_start < event.x < x_end:
            self._drag_mode = "move"
            self._drag_offset = time - self._start
        elif time < self._start:
            self._drag_mode = "start"
            self._start = max(0.0, min(time, self._end - self.MIN_LENGTH))
            self._emit(False)
        else:
            self._drag_mode = "end"
            self._end = min(self._duration, max(time, self._start + self.MIN_LENGTH))
            self._emit(False)

    def _on_shift_press(self, event) -> str:
        if not self._peaks or self._duration <= 0:
            return "break"
        if abs(event.x - self._x_for_time(self._start)) <= 8 or abs(
            event.x - self._x_for_time(self._end)
        ) <= 8:
            return "break"
        self._try_set_audition(self._time_for_x(event.x))
        return "break"

    def _try_set_audition(self, seconds: float) -> None:
        if self._start - 1e-9 <= seconds <= self._end + 1e-9:
            self._set_audition_internal(max(self._start, min(seconds, self._end)), notify=True)
            self.redraw()

    def _on_context_click(self, event) -> None:
        if not self._peaks or self._duration <= 0 or self._on_context is None:
            return
        seconds = self._time_for_x(event.x)
        if self._start - 1e-9 <= seconds <= self._end + 1e-9:
            self._on_context(seconds, event.x_root, event.y_root)

    def _on_mousewheel(self, event) -> str:
        if not self._peaks or self._duration <= 0:
            return "break"
        if event.state & 0x0004:  # Ctrl
            factor = 1.25 if event.delta > 0 else 1 / 1.25
            self.zoom(factor, center_x=event.x)
        else:
            direction = -1 if event.delta > 0 else 1
            self.pan(direction * 0.1 * (self._view_end - self._view_start))
        return "break"

    def _on_pan_start(self, event) -> None:
        if not self._peaks:
            return
        self._pan_x = float(event.x)
        self._pan_view_start = self._view_start
        self.configure(cursor="fleur")

    def _on_pan_motion(self, event) -> None:
        if self._pan_x is None or not self._peaks:
            return
        left, right, _top, _bottom, _axis = self._geometry()
        if right <= left:
            return
        seconds_per_pixel = (self._view_end - self._view_start) / (right - left)
        delta = -(event.x - self._pan_x) * seconds_per_pixel
        length = self._view_end - self._view_start
        start = max(0.0, min(self._pan_view_start + delta, self._duration - length))
        self.set_view(start, start + length)

    def _on_pan_end(self, _event) -> None:
        self._pan_x = None
        self.configure(cursor="crosshair")

    def _on_drag(self, event) -> None:
        if self._drag_mode is None or not self._peaks:
            return
        time = self._time_for_x(event.x)
        if self._drag_mode == "start":
            self._start = max(0.0, min(time, self._end - self.MIN_LENGTH))
        elif self._drag_mode == "end":
            self._end = min(self._duration, max(time, self._start + self.MIN_LENGTH))
        elif self._drag_mode == "move":
            length = self._end - self._start
            desired = time - self._drag_offset
            desired = max(0.0, min(desired, self._duration - length))
            self._start = desired
            self._end = desired + length
        self._emit(False)

    def _on_release(self, _event) -> None:
        if self._drag_mode is not None:
            self._drag_mode = None
            self._emit(True)

    def _on_motion(self, event) -> None:
        if not self._peaks:
            self.configure(cursor="crosshair")
            return
        if self._drag_mode is not None:
            return
        x_start = self._x_for_time(self._start)
        x_end = self._x_for_time(self._end)
        if event.y >= self.winfo_height() - 25 and not (
            abs(event.x - x_start) <= 8 or abs(event.x - x_end) <= 8
        ):
            self.configure(cursor="hand2")
        elif abs(event.x - x_start) <= 8 or abs(event.x - x_end) <= 8:
            self.configure(cursor="sb_h_double_arrow")
        elif x_start < event.x < x_end:
            self.configure(cursor="fleur")
        else:
            self.configure(cursor="crosshair")

    def _emit(self, final: bool) -> None:
        # Round display values to milliseconds while keeping drags fluid.
        self._start = round(self._start, 3)
        self._end = round(self._end, 3)
        self._clamp_audition_to_selection()
        self.redraw()
        if self._on_change:
            self._on_change(self._start, self._end, final)


class ProgressBar(tk.Canvas):
    def __init__(self, master, height: int = 7, bg: str = theme.SURFACE_ALT):
        super().__init__(
            master,
            height=height,
            bg=bg,
            highlightthickness=0,
            bd=0,
        )
        self._progress = 0.0
        self._height = height
        self.bind("<Configure>", lambda _e: self._draw())

    def set_progress(self, value: float) -> None:
        self._progress = max(0.0, min(1.0, value))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        height = self._height
        rounded_rectangle(
            self,
            0,
            0,
            width,
            height,
            height / 2,
            fill="#232B39",
            outline="",
        )
        if self._progress > 0:
            fill_width = max(height, width * self._progress)
            rounded_rectangle(
                self,
                0,
                0,
                fill_width,
                height,
                height / 2,
                fill=theme.ACCENT,
                outline="",
            )


class Tooltip:
    """Small delayed tooltip for truncated labels."""

    def __init__(self, widget: tk.Widget, text: str, delay: int = 600):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def update_text(self, text: str) -> None:
        self.text = text

    def _schedule(self, _event=None) -> None:
        self._cancel()
        if self.text:
            self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._tip is not None or not self.text or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            tip,
            text=self.text,
            bg="#252D3B",
            fg=theme.TEXT_SOFT,
            font=theme.Fonts.font(9),
            padx=10,
            pady=6,
            justify="left",
            wraplength=520,
        ).pack()
        self._tip = tip

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


class TimeEntry(tk.Frame):
    """A dark entry with a focus ring that looks native to the app."""

    def __init__(
        self,
        master,
        variable: tk.StringVar,
        width: int = 10,
        on_submit: Callable[[], None] | None = None,
        bg: str = theme.INPUT,
    ):
        super().__init__(master, bg=bg, highlightthickness=1, highlightbackground=theme.BORDER, bd=0)
        self._bg = bg
        self._on_submit = on_submit
        self.entry = tk.Entry(
            self,
            textvariable=variable,
            width=width,
            bg=bg,
            fg=theme.TEXT,
            insertbackground=theme.ACCENT,
            disabledbackground=bg,
            disabledforeground=theme.MUTED_DARK,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=theme.Fonts.font(10, family=theme.Fonts.mono),
            justify="center",
        )
        self.entry.pack(fill="x", padx=8, pady=6)
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<Up>", lambda _e: self._arrow(1))
        self.entry.bind("<Down>", lambda _e: self._arrow(-1))

    def _focus_in(self, _event=None) -> None:
        self.configure(highlightbackground=theme.ACCENT)

    def _focus_out(self, _event=None) -> None:
        self.configure(highlightbackground=theme.BORDER)
        self._submit()

    def _submit(self, _event=None) -> str:
        if self._on_submit:
            self._on_submit()
        return "break"

    def _arrow(self, direction: int) -> str:
        try:
            from .audio import parse_time

            value = parse_time(self.entry.get())
            step = 0.1 if direction > 0 else -0.1
            from .audio import format_duration

            self.entry.delete(0, "end")
            self.entry.insert(0, format_duration(max(0.0, value + step), precise=True))
            self._submit()
        except Exception:
            pass
        return "break"
