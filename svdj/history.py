"""Bounded snapshot-based undo / redo history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HistoryEntry:
    state: dict[str, Any]
    label: str


class HistoryManager:
    """Keep immutable project snapshots with human-readable action labels.

    The manager does not know how to capture or restore application state.
    Callers supply a fresh snapshot for each action and restore the returned
    snapshot when undoing or redoing.
    """

    def __init__(self, limit: int = 100):
        self.limit = max(1, int(limit))
        self._undo: list[HistoryEntry] = []
        self._redo: list[HistoryEntry] = []
        self._current: dict[str, Any] | None = None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def reset(self, state: dict[str, Any] | None = None) -> None:
        self._undo.clear()
        self._redo.clear()
        self._current = state

    def sync(self, state: dict[str, Any]) -> None:
        """Update the baseline after non-user metadata changes.

        Existing undo / redo entries are preserved, but the next recorded
        action compares against the new baseline. This is used when FFmpeg
        finishes probing and updates durations that are not user edits.
        """
        self._current = state

    def record(self, state: dict[str, Any], label: str) -> bool:
        """Record a new state if it differs from the current baseline."""
        if self._current is None:
            self._current = state
            return False
        if state == self._current:
            return False
        self._undo.append(HistoryEntry(self._current, label))
        if len(self._undo) > self.limit:
            del self._undo[0]
        self._current = state
        self._redo.clear()
        return True

    def undo(self, current_state: dict[str, Any]) -> HistoryEntry | None:
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._redo.append(HistoryEntry(current_state, entry.label))
        self._current = entry.state
        return entry

    def redo(self, current_state: dict[str, Any]) -> HistoryEntry | None:
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._undo.append(HistoryEntry(current_state, entry.label))
        if len(self._undo) > self.limit:
            del self._undo[0]
        self._current = entry.state
        return entry
