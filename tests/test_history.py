from __future__ import annotations

import unittest

from svdj.history import HistoryManager


class HistoryManagerTests(unittest.TestCase):
    def test_record_undo_redo(self):
        history = HistoryManager(limit=10)
        history.reset({"v": 1})
        self.assertTrue(history.record({"v": 2}, "加一"))
        self.assertTrue(history.can_undo)
        self.assertFalse(history.can_redo)
        self.assertEqual(history.undo_label, "加一")

        entry = history.undo({"v": 2})
        self.assertIsNotNone(entry)
        self.assertEqual(entry.state, {"v": 1})
        self.assertEqual(entry.label, "加一")
        self.assertFalse(history.can_undo)
        self.assertTrue(history.can_redo)

        entry = history.redo({"v": 1})
        self.assertIsNotNone(entry)
        self.assertEqual(entry.state, {"v": 2})
        self.assertTrue(history.can_undo)
        self.assertFalse(history.can_redo)

    def test_noop_not_recorded(self):
        history = HistoryManager(limit=10)
        history.reset({"v": 1})
        self.assertFalse(history.record({"v": 1}, "无变化"))
        self.assertFalse(history.can_undo)

    def test_new_action_clears_redo(self):
        history = HistoryManager(limit=10)
        history.reset({"v": 0})
        history.record({"v": 1}, "第一步")
        history.undo({"v": 1})
        self.assertTrue(history.can_redo)
        history.record({"v": 9}, "新分支")
        self.assertFalse(history.can_redo)

    def test_limit(self):
        history = HistoryManager(limit=2)
        history.reset({"v": 0})
        for value in (1, 2, 3):
            history.record({"v": value}, f"第 {value} 步")
        # Only the two newest actions are undoable.
        self.assertEqual(history.undo({"v": 3}).state, {"v": 2})
        self.assertEqual(history.undo({"v": 2}).state, {"v": 1})
        self.assertFalse(history.can_undo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
