from __future__ import annotations

import curses
import json
import math
import random
import time
import textwrap
from pathlib import Path

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
        self.items = repos(self.current)
        self.selected = next((i for i, item in enumerate(self.items) if item["path"] == str(self.current)), 0)
        self.snapshot = {}
        self.events = []
        self.error = ""
        self.last_refresh = 0.0
        self.frame = 0
        self.rng = random.Random(42)
        self.focus = "herds"
        self.scroll = {
            "herds": 0,
            "orchestration": 0,
            "activity": 0,
        }

    @property
    def path(self):
        return Path(self.items[self.selected]["path"])

    def refresh(self):
        self.items = repos(self.current)
        try:
            self.snapshot = self.control.snapshot(self.path)
            self.events = self.control.events(self.path, limit=20)
            self.error = ""
        except Exception as exc:
            self.snapshot = {}
            self.events = []
            self.error = str(exc)
        self.last_refresh = time.monotonic()

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
        put(screen, 1, w - 19, pulse + "  READ-ONLY LIVE")

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

            put(screen, top + 2, split + 2, "ACTIVITY" + (" ●" if self.focus == "activity" else ""), curses.A_BOLD)

            row = top + 4

            events = list(reversed(self.events))
            events = events[self.scroll["activity"]:]

            for event in events[: max(1, (bottom - row) // 2)]:
                when = event.get("timestamp_ms")
                stamp = time.strftime("%H:%M:%S", time.localtime(when / 1000)) if isinstance(when, int) else "--:--:--"

                put(screen, row, split + 2, stamp + "  " + str(event.get("type") or "event"))
                put(screen, row + 1, split + 4, str(event.get("actor") or ""), curses.A_DIM)

                row += 2

        if self.error:
            put(screen, bottom - 1, left + 3, "ERROR: " + self.error, curses.A_BOLD)

        focus_hint = {
            "herds": "[↑/↓] Select Herd",
            "orchestration": "[↑/↓] Scroll Agents",
            "activity": "[↑/↓] Scroll Events",
        }.get(self.focus, "[↑/↓] Navigate")

        put(screen, h - 2, 2, focus_hint + "   [TAB] Switch Pane   [X] Shutdown   [R] Refresh   [Q] Quit", curses.A_DIM)

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

            self.draw(screen)
            self.frame += 1

            key = screen.getch()

            if key in (ord("q"), ord("Q")):
                return

            if key in (ord("x"), ord("X")):
                self.control.shutdown(self.path)
                self.refresh()

            if key == ord("\t"):
                panes = ("herds", "orchestration", "activity")
                self.focus = panes[(panes.index(self.focus) + 1) % len(panes)]

            elif key in (ord("r"), ord("R")):
                self.refresh()

            elif key == curses.KEY_UP:
                if self.focus == "herds" and self.items:
                    self.selected = (self.selected - 1) % len(self.items)
                    self.refresh()
                else:
                    self.scroll[self.focus] = max(0, self.scroll[self.focus] - 1)

            elif key == curses.KEY_DOWN:
                if self.focus == "herds" and self.items:
                    self.selected = (self.selected + 1) % len(self.items)
                    self.refresh()
                else:
                    self.scroll[self.focus] = min(self.scroll[self.focus] + 1, 999)


def run(repo="."):
    curses.wrapper(MissionControl(repo).loop)


run_tui = run
