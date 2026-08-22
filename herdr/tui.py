from __future__ import annotations

import curses
import json
import math
import random
import threading
import time
import textwrap
import uuid
from pathlib import Path

from mission_control.approvals import (
    ApprovalExecutionService,
    ApprovalQueue,
    OperatorApprovalService,
)
from mission_control.execution import (
    CommandExecutionEngine,
    MissionControlExecutionService,
)
from mission_control.models import (
    OperatorReviewService,
    create_operator_provider,
)
from mission_control.session import (
    GhosttySessionDriver,
)
from mission_control.state import (
    LIFECYCLE_ACTIVE,
    MissionControlStateStore,
)

from .control_plane import HerdrControlPlane
from .registry import registry_load


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def put(screen, y, x, text, attr=0):
    try:
        h, w = screen.getmaxyx()
        if 0 <= y < h and 0 <= x < w:
            screen.addstr(y, x, str(text)[: max(0, w - x - 1)], attr)
    except curses.error:
        pass


def put_clipped(screen, y, x, right, text, attr=0):
    try:
        h, w = screen.getmaxyx()
        if 0 <= y < h and 0 <= x < w:
            limit = min(w - 1, right)
            screen.addstr(y, x, str(text)[: max(0, limit - x)], attr)
    except curses.error:
        pass


def put_wrapped(screen, y, x, width, text, attr=0, max_lines=5):
    for i, line in enumerate(textwrap.wrap(str(text), width=max(1, width))[:max_lines]):
        put_clipped(screen, y + i, x, x + width, line, attr)


def wrap_lines(text, width):
    return textwrap.wrap(str(text or ""), width=max(1, width))


def box(screen, top, left, bottom, right, title):
    put(screen, top, left, "┌" + "─" * max(0, right - left - 1) + "┐")
    for row in range(top + 1, bottom):
        put(screen, row, left, "│")
        put(screen, row, right, "│")
    put(screen, bottom, left, "└" + "─" * max(0, right - left - 1) + "┘")

    title_text = " " + title + " "
    title_x = left + 2
    put(screen, top, title_x, title_text, curses.A_BOLD)


def repos(current):
    data = registry_load().get("repos", {})
    items = [{"alias": alias, "path": str(Path(value["path"]).expanduser().resolve())} for alias, value in sorted(data.items())]
    here = str(current.resolve())
    if not any(item["path"] == here for item in items):
        items.insert(0, {"alias": current.name, "path": here})
    return items


def repo_state(path):
    task = read_json(path / ".herd" / "state" / "task.json", {})
    status = str(task.get("status") or "IDLE").upper()
    if status == "ACTIVE":
        return "ACTIVE"
    if (path / ".herd" / "state" / "runtime.json").exists():
        return "READY"
    return "OFFLINE"


def role_model(path, logical):
    config = read_json(path / ".herd" / "herd.config.json", {})
    base = logical.rstrip("0123456789").lower()
    role = config.get("roles", {}).get(base, {})
    kind = str(role.get("kind") or "unknown")
    args = list(role.get("args") or [])
    model = ""
    for flag in ("--model", "-m"):
        if flag in args and args.index(flag) + 1 < len(args):
            model = str(args[args.index(flag) + 1])
            break
    return kind + (" · " + model if model else "")


class MissionControl:
    def __init__(self, repo):
        self.current = Path(repo).expanduser().resolve()
        self.control = HerdrControlPlane()

        self.session_driver = GhosttySessionDriver()
        self.approvals = ApprovalQueue()

        self.execution_engine = CommandExecutionEngine(
            self.session_driver
        )
        self.execution_service = MissionControlExecutionService(
            self.execution_engine
        )

        self.operator_review = OperatorReviewService(
            create_operator_provider()
        )
        self.operator_approval = OperatorApprovalService(
            self.approvals,
            self.operator_review,
        )
        self.approval_execution = ApprovalExecutionService(
            self.approvals,
            self.execution_service,
        )

        self.items = repos(self.current)
        self.selected = next((i for i, item in enumerate(self.items) if item["path"] == str(self.current)), 0)
        self.snapshot = {}
        self.events = []
        self.error = ""
        self.refresh_error = ""
        self.notice = ""
        self.last_refresh = 0.0
        self.frame = 0
        self.rng = random.Random(42)

        self.approval_selected = 0
        self.review_threads = {}
        self.execution_threads = {}
        self.ui_lock = threading.RLock()

        self.focus = "herds"
        self.scroll = {
            "herds": 0,
            "orchestration": 0,
            "activity": 0,
            "approvals": 0,
        }

    @property
    def path(self):
        return Path(self.items[self.selected]["path"])

    def refresh(self):
        self.items = repos(self.current)
        try:
            self.snapshot = self.control.snapshot(self.path)
            self.events = self.control.events(self.path, limit=20)
            self.refresh_error = ""
        except Exception as exc:
            self.snapshot = {}
            self.events = []
            self.refresh_error = str(exc)
        self.last_refresh = time.monotonic()

    def _review_worker(
        self,
        repo_path,
        herd_id,
    ):
        review_started = False

        def on_started(execution_id):
            nonlocal review_started
            review_started = True

            with self.ui_lock:
                self.notice = (
                    f"{herd_id}: operator reviewing "
                    f"{execution_id}..."
                )
                self.error = ""

        try:
            item = self.operator_approval.review_latest_handoff(
                repo_path,
                herd_id=herd_id,
                on_started=on_started,
            )

            if item is not None:
                with self.ui_lock:
                    self.notice = (
                        f"{herd_id}: approval ready "
                        f"({len(item.commands)} commands)"
                    )
                    self.error = ""
            elif review_started:
                with self.ui_lock:
                    self.notice = (
                        f"{herd_id}: review complete — "
                        "no action required"
                    )
                    self.error = ""
        except Exception as exc:
            with self.ui_lock:
                self.error = (
                    f"{herd_id} operator review failed: {exc}"
                )

    def _poll_operator_reviews(self):
        for item in self.items:
            repo = Path(item["path"]).resolve()

            try:
                state = MissionControlStateStore(repo).load()
            except Exception:
                continue

            if (
                state is None
                or state.lifecycle != LIFECYCLE_ACTIVE
            ):
                continue

            herd_id = state.herd_id
            thread = self.review_threads.get(herd_id)

            if thread is not None and thread.is_alive():
                continue

            thread = threading.Thread(
                target=self._review_worker,
                args=(repo, herd_id),
                daemon=True,
                name=f"mc-review-{herd_id}",
            )
            self.review_threads[herd_id] = thread
            thread.start()

    def _pending_approvals(self):
        pending = self.approvals.pending()

        if not pending:
            self.approval_selected = 0
            self.scroll["approvals"] = 0
            return pending

        self.approval_selected = min(
            self.approval_selected,
            len(pending) - 1,
        )

        return pending

    def _selected_approval(self):
        pending = self._pending_approvals()

        if not pending:
            return None

        return pending[self.approval_selected]

    def _execution_worker(
        self,
        approval_id,
    ):
        try:
            item = self.approvals.get(
                approval_id
            )
            store = MissionControlStateStore(
                item.repo_path
            )
            state = store.load()

            if state is None:
                raise RuntimeError(
                    "No durable Mission Control Herd state exists"
                )

            if state.lifecycle != LIFECYCLE_ACTIVE:
                raise RuntimeError(
                    "Mission Control Herd is not active"
                )

            if not state.terminal_id:
                raise RuntimeError(
                    "Active Herd has no recorded Ghostty terminal"
                )

            session = self.session_driver.reconnect(
                herd_id=state.herd_id,
                terminal_id=state.terminal_id,
                repo_path=item.repo_path,
            )

            execution_id = (
                "mc-" + uuid.uuid4().hex
            )

            result = self.approval_execution.approve_and_execute(
                approval_id,
                session,
                execution_id=execution_id,
            )

            with self.ui_lock:
                self.notice = (
                    f"{item.herd_id}: execution "
                    f"{'completed' if result.succeeded else 'failed'} "
                    f"(exit {result.exit_code})"
                )
                self.error = ""
        except Exception as exc:
            with self.ui_lock:
                self.error = (
                    f"Approve & Execute failed: {exc}"
                )

    def _start_approval_execution(
        self,
        approval_id,
    ):
        item = self.approvals.get(
            approval_id
        )
        thread = self.execution_threads.get(
            item.herd_id
        )

        if thread is not None and thread.is_alive():
            raise RuntimeError(
                f"Herd {item.herd_id} already has "
                "an execution in progress"
            )

        thread = threading.Thread(
            target=self._execution_worker,
            args=(approval_id,),
            daemon=True,
            name=f"mc-execute-{item.herd_id}",
        )
        self.execution_threads[item.herd_id] = thread
        thread.start()

    def draw(self, screen):
        screen.erase()
        h, w = screen.getmaxyx()

        if h < 22 or w < 90:
            put(screen, 1, 2, "∞ DODGING INFINITY", curses.A_BOLD)
            put(screen, 3, 2, "Resize Ghostty to at least 90x22.")
            put(screen, 5, 2, "[Q] Quit")
            screen.refresh()
            return

        stars = ("·", "⋆", "✦", "•")
        for i in range(max(12, w // 7)):
            x = (i * 23 + 11) % max(1, w - 2)
            y = i % 3
            glyph = stars[(i + self.frame // 6) % len(stars)]
            put(screen, y, x, glyph, curses.A_DIM)

        center_x = w // 2
        for i in range(34):
            t = (i / 34.0) * math.tau
            scale = min(16, max(8, w // 9))
            x = center_x + int(scale * math.sin(t))
            y = 1 + int(1.35 * math.sin(t) * math.cos(t))
            glyph = "✦" if (i + self.frame // 4) % 11 == 0 else "·"
            put(screen, y, x, glyph, curses.A_DIM)

        cycle = self.frame % 54
        if cycle < 18:
            star_x = w - 7 - cycle * 3
            star_y = cycle // 7
            if star_x > 2:
                put(screen, star_y, star_x, "✦")
                if star_x + 2 < w:
                    put(screen, star_y, star_x + 2, "╱", curses.A_DIM)
                if star_x + 4 < w:
                    put(screen, star_y, star_x + 4, "·", curses.A_DIM)

        pulse = "●" if self.frame % 8 < 4 else "◉"
        put(screen, 1, 2, "∞  DODGING INFINITY", curses.A_BOLD)
        put(screen, 2, 5, "MISSION CONTROL", curses.A_DIM)
        put(screen, 1, w - 19, pulse + "  CONTROL LIVE")

        top = 4
        bottom = h - 3
        left = min(31, max(24, w // 4))

        herd_lines = []

        for i, item in enumerate(self.items):
            selected = i == self.selected
            attr = curses.A_REVERSE if selected else 0
            marker = "› " if selected else "  "
            state = repo_state(Path(item["path"]))
            state_mark = ("●" if self.frame % 8 < 4 else "◉") if state == "ACTIVE" else "·"

            herd_lines.append((marker + item["alias"], attr | curses.A_BOLD))
            herd_lines.append((state_mark + " " + state, attr | curses.A_DIM))
            herd_lines.append(("", 0))

        herd_total = len(self.items)
        herd_pos = min(self.scroll["herds"] + 1, max(1, herd_total))

        inner = left + 5
        split = inner + max(38, int((w - inner - 4) * 0.58))

        box(screen, top, 0, bottom, left, "HERDS" + (" ●" if self.focus == "herds" else ""))
        box(screen, top, left + 2, bottom, split, "ACTIVE ORCHESTRATION" + (" ●" if self.focus == "orchestration" else ""))

        row = top + 2
        for text, attr in herd_lines[self.scroll["herds"]:]:
            if row >= bottom - 1:
                break
            put(screen, row, 2, text, attr)
            row += 1

        snap_repo = self.snapshot.get("repo", {})
        runtime = self.snapshot.get("runtime", {})
        task = self.snapshot.get("task") or {}
        agents = runtime.get("agents", [])

        name = snap_repo.get("name") or self.path.name
        path_text = snap_repo.get("path") or str(self.path)

        cursor = top + 2

        put(screen, cursor, inner, name, curses.A_BOLD)
        cursor += 1

        put_clipped(screen, cursor, inner, split - 2, path_text, curses.A_DIM)
        cursor += 2

        put(screen, cursor, inner, "Runtime  " + str(runtime.get("status") or "UNKNOWN"), curses.A_BOLD)
        cursor += 2

        if task:
            put(screen, cursor, inner, "Objective  " + str(task.get("status") or "UNKNOWN"), curses.A_BOLD)
            cursor += 1

        content = []

        if task:
            description = str(task.get("description") or "")
            for line in wrap_lines(description, split - inner - 2):
                content.append((line, curses.A_DIM))

            content.append(("", 0))

        orchestration_lines = content + [("", 0)]

        for agent in agents:
            logical = str(agent.get("logical_name") or "agent")
            status = str(agent.get("status") or "unknown").upper()

            orchestration_lines.append((
                "◆ " + logical.upper() + " [" + status + "]",
                curses.A_BOLD,
            ))
            orchestration_lines.append((
                "  " + role_model(self.path, logical),
                curses.A_DIM,
            ))
            orchestration_lines.append((
                "│",
                curses.A_DIM,
            ))
            orchestration_lines.append(("", 0))

        visible = orchestration_lines[self.scroll["orchestration"]:]

        row = cursor
        for text, attr in visible:
            if row >= bottom - 1:
                break

            put_clipped(screen, row, inner, split - 2, text, attr)
            row += 1

        if not agents:
            put(screen, row, inner, "∞  No active agent topology.", curses.A_DIM)

        if split < w - 24:
            for y in range(top + 1, bottom):
                put(screen, y, split, "│", curses.A_DIM)

            right_x = split + 2
            right_edge = w - 2
            pending = self._pending_approvals()

            approval_title = (
                f"APPROVAL QUEUE [{len(pending)}]"
                + (" ●" if self.focus == "approvals" else "")
            )
            put(
                screen,
                top + 2,
                right_x,
                approval_title,
                curses.A_BOLD,
            )

            approval_top = top + 4
            activity_top = max(
                approval_top + 6,
                top + ((bottom - top) // 2),
            )

            if pending:
                selected_approval = pending[
                    self.approval_selected
                ]

                put_clipped(
                    screen,
                    approval_top,
                    right_x,
                    right_edge,
                    "› " + selected_approval.herd_id,
                    curses.A_BOLD,
                )

                approval_lines = []

                for line in wrap_lines(
                    selected_approval.explanation,
                    max(1, right_edge - right_x),
                ):
                    approval_lines.append(
                        (line, curses.A_DIM)
                    )

                approval_lines.append(("", 0))

                for index, command in enumerate(
                    selected_approval.commands,
                    start=1,
                ):
                    approval_lines.append(
                        (
                            f"{index}. {command}",
                            0,
                        )
                    )

                visible_approvals = approval_lines[
                    self.scroll["approvals"]:
                ]

                row = approval_top + 1

                for line, attr in visible_approvals:
                    if row >= activity_top - 1:
                        break

                    put_clipped(
                        screen,
                        row,
                        right_x,
                        right_edge,
                        line,
                        attr,
                    )
                    row += 1
            else:
                put(
                    screen,
                    approval_top,
                    right_x,
                    "∞  No actions need you.",
                    curses.A_DIM,
                )

            for x in range(split + 1, w - 1):
                put(
                    screen,
                    activity_top - 1,
                    x,
                    "─",
                    curses.A_DIM,
                )

            put(
                screen,
                activity_top,
                right_x,
                "ACTIVITY"
                + (
                    " ●"
                    if self.focus == "activity"
                    else ""
                ),
                curses.A_BOLD,
            )

            row = activity_top + 2

            events = list(reversed(self.events))
            events = events[
                self.scroll["activity"]:
            ]

            for event in events[
                : max(1, (bottom - row) // 2)
            ]:
                when = event.get("timestamp_ms")
                stamp = (
                    time.strftime(
                        "%H:%M:%S",
                        time.localtime(when / 1000),
                    )
                    if isinstance(when, int)
                    else "--:--:--"
                )

                put_clipped(
                    screen,
                    row,
                    right_x,
                    right_edge,
                    stamp
                    + "  "
                    + str(event.get("type") or "event"),
                )
                put_clipped(
                    screen,
                    row + 1,
                    right_x + 2,
                    right_edge,
                    str(event.get("actor") or ""),
                    curses.A_DIM,
                )

                row += 2

        visible_error = self.error or self.refresh_error

        if visible_error:
            put(
                screen,
                bottom - 1,
                left + 3,
                "ERROR: " + visible_error,
                curses.A_BOLD,
            )
        elif self.notice:
            put_clipped(
                screen,
                bottom - 1,
                left + 3,
                w - 2,
                self.notice,
                curses.A_DIM,
            )

        focus_hint = {
            "herds": "[↑/↓] Select Herd",
            "orchestration": "[↑/↓] Scroll Agents",
            "approvals": "[↑/↓] Select Approval",
            "activity": "[↑/↓] Scroll Events",
        }.get(self.focus, "[↑/↓] Navigate")

        action_hint = (
            "   [A] Approve & Execute"
            if self.focus == "approvals"
            else ""
        )

        put(
            screen,
            h - 2,
            2,
            focus_hint
            + action_hint
            + "   [TAB] Switch Pane"
            + "   [X] Shutdown"
            + "   [R] Refresh"
            + "   [Q] Quit",
            curses.A_DIM,
        )

        screen.refresh()

    def loop(self, screen):
        try:
            curses.curs_set(0)
        except curses.error:
            pass

        screen.timeout(120)
        self.refresh()

        while True:
            if time.monotonic() - self.last_refresh >= 1.0:
                self.refresh()
                self._poll_operator_reviews()

            self.draw(screen)
            self.frame += 1

            key = screen.getch()

            if key in (ord("q"), ord("Q")):
                return

            if key in (ord("x"), ord("X")):
                self.control.shutdown(self.path)
                self.refresh()

            if key == ord("\t"):
                panes = (
                    "herds",
                    "orchestration",
                    "approvals",
                    "activity",
                )
                self.focus = panes[(panes.index(self.focus) + 1) % len(panes)]

            elif key in (ord("r"), ord("R")):
                self.refresh()

            elif (
                key in (ord("a"), ord("A"))
                and self.focus == "approvals"
            ):
                approval = self._selected_approval()

                if approval is None:
                    self.notice = "No pending approval selected."
                else:
                    try:
                        self._start_approval_execution(
                            approval.approval_id
                        )
                        self.notice = (
                            f"{approval.herd_id}: "
                            "Approve & Execute started"
                        )
                        self.error = ""
                    except Exception as exc:
                        self.error = (
                            f"Approve & Execute failed: {exc}"
                        )

            elif key == curses.KEY_UP:
                if self.focus == "herds" and self.items:
                    self.selected = (
                        self.selected - 1
                    ) % len(self.items)
                    self.refresh()
                elif self.focus == "approvals":
                    pending = self._pending_approvals()

                    if pending:
                        self.approval_selected = (
                            self.approval_selected - 1
                        ) % len(pending)
                        self.scroll["approvals"] = 0
                else:
                    self.scroll[self.focus] = max(
                        0,
                        self.scroll[self.focus] - 1,
                    )

            elif key == curses.KEY_DOWN:
                if self.focus == "herds" and self.items:
                    self.selected = (
                        self.selected + 1
                    ) % len(self.items)
                    self.refresh()
                elif self.focus == "approvals":
                    pending = self._pending_approvals()

                    if pending:
                        self.approval_selected = (
                            self.approval_selected + 1
                        ) % len(pending)
                        self.scroll["approvals"] = 0
                else:
                    self.scroll[self.focus] = min(
                        self.scroll[self.focus] + 1,
                        999,
                    )


def run(repo="."):
    curses.wrapper(MissionControl(repo).loop)


run_tui = run
