"""Admission control, copied from bdc-assist (r_assist/api.py) unchanged: at most `running` panel turns at once,
up to `waiting` more wait for a slot, and beyond that a turn gets a 503."""

import asyncio
from collections import deque


class Full(Exception):
    """No free chat slot and no place left in line."""


class Admission:
    """At most `running` chats at once; up to `waiting` more wait in line, first come first served, and
    hear their place in it; beyond that a chat is refused. Sized to the slowest backend: a chat let in
    beyond what that serves only waits there instead, where nobody can tell it how long. 0 running =
    no cap."""

    def __init__(self, running: int, waiting: int):
        self.running, self.waiting = running, waiting
        self.busy, self.line = 0, deque()
        self.avg = None  # moving average of the seconds a chat holds its slot: the wait estimate

    def full(self) -> bool:
        return bool(self.running) and self.busy >= self.running and len(self.line) >= self.waiting

    def eta(self, place: int) -> float | None:
        """Seconds until the chat at this place in line starts: a slot frees every avg/running seconds."""
        return None if self.avg is None else place * self.avg / self.running

    async def wait_turn(self):
        """Ends holding a slot (hand it back with release); until then, yields the place in line
        whenever it changes. Raises Full if the line is full: checked as it joins, with no await in
        between, so a burst of requests can't all pass a check made before any of them joined."""
        if not self.running:
            return
        if self.busy < self.running and not self.line:
            self.busy += 1
            return
        if len(self.line) >= self.waiting:
            raise Full
        turn = asyncio.get_running_loop().create_future()
        self.line.append(turn)
        try:
            shown = None
            while not turn.done():
                place = self.line.index(turn) + 1
                if place != shown:
                    shown = place
                    yield place
                await asyncio.wait({turn}, timeout=1)
        except BaseException:  # gave up waiting (client gone): leave the line, or pass on a slot just handed over
            if turn.done():
                self.release()
            else:
                self.line.remove(turn)
            raise

    def release(self, held: float | None = None):
        if not self.running:
            return
        if held is not None:
            self.avg = held if self.avg is None else 0.8 * self.avg + 0.2 * held
        if self.line:
            self.line.popleft().set_result(None)  # the slot passes straight to the next in line
        else:
            self.busy -= 1
