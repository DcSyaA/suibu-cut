"""Tiny WAV preview player built on the Windows winsound module."""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from typing import Callable

try:
    import winsound
except ImportError:  # pragma: no cover - only Windows is supported
    winsound = None  # type: ignore


class PreviewPlayer:
    def __init__(
        self,
        root: tk.Misc,
        on_tick: Callable[[float], None] | None = None,
        on_finished: Callable[[], None] | None = None,
    ):
        self.root = root
        self.on_tick = on_tick
        self.on_finished = on_finished
        self._after_id: str | None = None
        self._started = 0.0
        self._duration = 0.0
        self._playing = False

    @property
    def is_playing(self) -> bool:
        return self._playing

    def play(self, wav_path: str | Path, duration: float) -> None:
        if winsound is None:
            raise RuntimeError("当前系统不支持试听播放。")
        self.stop()
        path = Path(wav_path)
        if not path.is_file():
            raise RuntimeError("试听文件尚未准备好。")
        winsound.PlaySound(
            str(path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
        self._started = time.monotonic()
        self._duration = max(0.05, duration)
        self._playing = True
        self._tick()

    def _tick(self) -> None:
        if not self._playing:
            return
        elapsed = time.monotonic() - self._started
        if elapsed >= self._duration:
            self.stop()
            if self.on_finished:
                self.on_finished()
            return
        if self.on_tick:
            self.on_tick(min(elapsed, self._duration))
        self._after_id = self.root.after(60, self._tick)

    def stop(self, notify: bool = False) -> None:
        was_playing = self._playing
        self._playing = False
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if winsound is not None:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except RuntimeError:
                pass
        if notify and was_playing and self.on_finished:
            self.on_finished()
