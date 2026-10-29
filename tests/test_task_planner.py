"""TaskTracker invariants."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from task_planner import TaskTracker

class TaskTrackerTests(unittest.TestCase):
    def make_tracker(self):
        return TaskTracker("Research and report", [
            {"id":"research","description":"Research"},
            {"id":"report","description":"Report"},
        ])

    def test_done_requires_completion(self):
        t = self.make_tracker()
        self.assertFalse(t.validate_done()[0])
        self.assertTrue(t.complete_task("research", "Collected")[0])
        self.assertFalse(t.validate_done()[0])
        self.assertTrue(t.complete_task("report", "Saved")[0])
        self.assertTrue(t.validate_done()[0])

    def test_completed_work_survives_replan(self):
        t = self.make_tracker()
        t.complete_task("research", "Collected")
        t.apply_replan([
            {"id":"research","description":"Do not repeat"},
            {"id":"review","description":"Review"},
        ])
        self.assertEqual(t.get_task("research").finding, "Collected")
        self.assertEqual(t.current_task.id, "review")
        self.assertFalse(t.validate_done()[0])

    def test_inject_next_before_pending(self):
        t = self.make_tracker()
        t.start_task("research")
        injected = t.inject_task("Check citations", priority="next")
        self.assertEqual([s.id for s in t.tasks], ["research",injected.id,"report"])

    def test_unknown_task_cannot_complete(self):
        t = self.make_tracker()
        self.assertFalse(t.complete_task("unknown", "fake")[0])

if __name__ == "__main__":
    unittest.main()
