"""A6 reference implementation — shared by every service.

Config lives in platform/resilience.yaml; behaviour lives here. Services import
this rather than each inventing their own breaker, so "open at 5 failures" means
the same thing in the orchestrator and the TTS gateway.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Protocol, TypeVar

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "resilience.yaml"
T = TypeVar("T")


def load_config(path: Path | None = None) -> dict:
    return yaml.safe_load((path or CONFIG).read_text())


# --------------------------------------------------------------------------- #
# Circuit breaker
# --------------------------------------------------------------------------- #
class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class BreakerOpen(Exception):
    """Raised instead of calling a provider known to be failing."""


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    timeout_threshold: int = 3
    window_s: float = 30.0
    open_duration_s: float = 15.0
    half_open_max_calls: int = 1
    _clock: Callable[[], float] = time.monotonic

    _state: State = field(default=State.CLOSED, init=False)
    _events: deque = field(default_factory=deque, init=False)   # (ts, kind)
    _opened_at: float = field(default=0.0, init=False)
    _half_open_calls: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    # -- introspection: an invisible breaker is a debugging nightmare -------
    @property
    def state(self) -> State:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _prune(self) -> None:
        cutoff = self._clock() - self.window_s
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def _maybe_half_open(self) -> None:
        if (self._state is State.OPEN
                and self._clock() - self._opened_at >= self.open_duration_s):
            self._state = State.HALF_OPEN
            self._half_open_calls = 0

    def _trip(self) -> None:
        self._state = State.OPEN
        self._opened_at = self._clock()
        self._events.clear()

    def allow(self) -> bool:
        with self._lock:
            self._maybe_half_open()
            if self._state is State.CLOSED:
                return True
            if self._state is State.HALF_OPEN:
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._events.clear()
            self._state = State.CLOSED
            self._half_open_calls = 0

    def record_failure(self, timeout: bool = False) -> None:
        with self._lock:
            if self._state is State.HALF_OPEN:
                self._trip()          # probe failed — straight back to open
                return
            self._events.append((self._clock(), "timeout" if timeout else "failure"))
            self._prune()
            failures = sum(1 for _, k in self._events if k == "failure")
            timeouts = sum(1 for _, k in self._events if k == "timeout")
            if failures >= self.failure_threshold or timeouts >= self.timeout_threshold:
                self._trip()

    def call(self, fn: Callable[[], T]) -> T:
        if not self.allow():
            raise BreakerOpen(f"{self.name} breaker is {self.state.value}")
        try:
            out = fn()
        except TimeoutError:
            self.record_failure(timeout=True)
            raise
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return out


# --------------------------------------------------------------------------- #
# Fallback chain
# --------------------------------------------------------------------------- #
class Provider(Protocol):
    name: str
    def __call__(self): ...


@dataclass
class ChainResult:
    value: object | None
    provider: str
    attempts: list[tuple[str, str]]     # (provider, outcome)
    exhausted: bool = False


def run_chain(providers: Iterable[tuple[str, Callable[[], T]]],
              breakers: dict[str, CircuitBreaker] | None = None,
              terminal: str | None = None) -> ChainResult:
    """Try each provider in order. `terminal` names a provider that represents
    a SUCCESSFUL degraded outcome (tts: text_only) rather than a failure."""
    breakers = breakers or {}
    attempts: list[tuple[str, str]] = []
    for name, fn in providers:
        b = breakers.get(name)
        if b is not None and not b.allow():
            attempts.append((name, "breaker_open"))
            continue
        try:
            value = b.call(fn) if b else fn()
        except BreakerOpen:
            attempts.append((name, "breaker_open"))
        except TimeoutError:
            attempts.append((name, "timeout"))
        except Exception as e:                                   # noqa: BLE001
            attempts.append((name, f"error:{type(e).__name__}"))
        else:
            attempts.append((name, "ok"))
            return ChainResult(value, name, attempts)
    if terminal:
        attempts.append((terminal, "degraded"))
        return ChainResult(None, terminal, attempts)
    return ChainResult(None, "none", attempts, exhausted=True)


# --------------------------------------------------------------------------- #
# Bounded queue with the three overflow policies
# --------------------------------------------------------------------------- #
class QueueFull(Exception):
    """Raised by the `block` policy — used for results that must never drop."""


class BoundedQueue:
    def __init__(self, max_size: int, overflow: str) -> None:
        if overflow not in ("drop_oldest", "pause_upstream", "block"):
            raise ValueError(f"unknown overflow policy: {overflow}")
        self.max_size, self.overflow = max_size, overflow
        self._items: deque = deque()
        self.dropped = 0
        self.paused = False

    def __len__(self) -> int:
        return len(self._items)

    def put(self, item) -> bool:
        """True if accepted. False means upstream should pause."""
        if len(self._items) < self.max_size:
            self._items.append(item)
            return True
        if self.overflow == "drop_oldest":
            self._items.popleft()
            self._items.append(item)
            self.dropped += 1
            return True
        if self.overflow == "pause_upstream":
            self.paused = True
            return False
        raise QueueFull(f"queue full ({self.max_size}) and policy is block")

    def get(self):
        item = self._items.popleft() if self._items else None
        if self.paused and len(self._items) < self.max_size:
            self.paused = False
        return item
