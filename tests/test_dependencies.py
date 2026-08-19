import json
import tempfile
import unittest
from pathlib import Path

from herdr.dependencies import (
    assert_child_dependencies_complete,
    child_dependencies,
)
from herdr.instance import HerdrInstance


class HerdrDependencyTests(unittest.TestCase):
    def make_parent_and_child(
        self,
        *,
        child_status="ACTIVE",
    ):
        temp = tempfile.TemporaryDirectory()

        root = Path(
            temp.name
        )

        parent = (
            root
            / "parent"
        )

        child = (
            root
            / "child"
        )

        parent_state = (
            parent
            / ".herd"
            / "state"
        )

        child_state = (
            child
            / ".herd"
            / "state"
        )

        parent_state.mkdir(
            parents=True
        )

        child_state.mkdir(
            parents=True
        )

        (
            parent
            / ".herd"
            / "herd.config.json"
        ).write_text(
            json.dumps(
                {
                    "version": 4,
                }
            )
        )

        (
            parent_state
            / "task.json"
        ).write_text(
            json.dumps(
                {
                    "id": "parent-task",
                    "status": "ACTIVE",
                }
            )
        )

        (
            child_state
            / "task.json"
        ).write_text(
            json.dumps(
                {
                    "id": "child-task",
                    "status": child_status,
                }
            )
        )

        (
            parent_state
            / "children.json"
        ).write_text(
            json.dumps(
                {
                    "version": 1,
                    "children": [
                        {
                            "parent_task_id": "parent-task",
                            "dependency": True,
                            "repo": str(child),
                            "task_id": "child-task",
                        }
                    ],
                }
            )
        )

        return (
            temp,
            HerdrInstance(parent),
        )

    def test_active_child_is_unresolved(self):
        temp, parent = self.make_parent_and_child(
            child_status="ACTIVE"
        )

        self.addCleanup(
            temp.cleanup
        )

        dependencies = child_dependencies(
            parent,
            "parent-task",
        )

        self.assertEqual(
            dependencies[0]["current_status"],
            "ACTIVE",
        )

        with self.assertRaises(RuntimeError):
            assert_child_dependencies_complete(
                parent,
                "parent-task",
            )

    def test_complete_child_satisfies_dependency(self):
        temp, parent = self.make_parent_and_child(
            child_status="COMPLETE"
        )

        self.addCleanup(
            temp.cleanup
        )

        assert_child_dependencies_complete(
            parent,
            "parent-task",
        )

    def test_other_parent_task_children_are_ignored(self):
        temp, parent = self.make_parent_and_child(
            child_status="ACTIVE"
        )

        self.addCleanup(
            temp.cleanup
        )

        dependencies = child_dependencies(
            parent,
            "different-parent-task",
        )

        self.assertEqual(
            dependencies,
            [],
        )


if __name__ == "__main__":
    unittest.main()
