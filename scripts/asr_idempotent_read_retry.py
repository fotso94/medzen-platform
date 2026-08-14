#!/usr/bin/env python3
"""Typed, bounded retries for explicitly allowlisted idempotent reads.

The retry boundary deliberately accepts only ``TransientReadFault``.  Callers
must classify a transport failure at the external-read boundary; validation,
identity, digest, policy and finding errors pass through unchanged and are
therefore never retried.
"""

from __future__ import annotations

import errno
import socket
import time
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable, TypeVar


READ_OPERATIONS = frozenset(
    {
        "ECR_PULL_BACK",
        "SCOUT_DATABASE_READ",
        "S3_READ",
    }
)
TRANSIENT_CATEGORIES = frozenset({"CONNECTION_RESET", "TIMEOUT", "DNS_BLIP"})
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = (1.0, 2.0)

T = TypeVar("T")


class ReadRetryConfigurationError(ValueError):
    pass


class TransientReadFault(RuntimeError):
    """A transport-only failure classified at an allowlisted read boundary."""

    def __init__(self, operation: str, category: str):
        if operation not in READ_OPERATIONS:
            raise ReadRetryConfigurationError(f"read operation is not allowlisted: {operation}")
        if category not in TRANSIENT_CATEGORIES:
            raise ReadRetryConfigurationError(f"transient category is not allowlisted: {category}")
        super().__init__(f"{operation}:{category}")
        self.operation = operation
        self.category = category


class TransientReadRetryExhausted(RuntimeError):
    def __init__(self, operation: str, audit: dict[str, Any]):
        super().__init__(f"typed transient retry exhausted for {operation}")
        self.reason_code = "TRANSIENT_IDEMPOTENT_READ_RETRY_EXHAUSTED"
        self.operation = operation
        self.audit = audit


@dataclass(frozen=True)
class RetryPolicy:
    maximum_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS
    hard_cap_seconds: float = 7200.0

    def validate(self) -> None:
        if self.maximum_attempts != 3:
            raise ReadRetryConfigurationError("read retry maximum must be exactly three attempts")
        if len(self.backoff_seconds) != self.maximum_attempts - 1:
            raise ReadRetryConfigurationError("read retry backoff count differs")
        if any(value <= 0 for value in self.backoff_seconds):
            raise ReadRetryConfigurationError("read retry backoff must be positive")
        if tuple(sorted(self.backoff_seconds)) != self.backoff_seconds:
            raise ReadRetryConfigurationError("read retry backoff must be non-decreasing")
        if self.hard_cap_seconds <= sum(self.backoff_seconds):
            raise ReadRetryConfigurationError("read retry hard cap is not usable")


def classify_transport_exception(operation: str, exc: BaseException) -> TransientReadFault | None:
    """Return a typed fault only for the three approved transport classes.

    The classification is intentionally shallow.  A validation exception with
    a transient exception in its causal chain is still a validation failure and
    must not be retried.
    """

    if operation not in READ_OPERATIONS:
        raise ReadRetryConfigurationError(f"read operation is not allowlisted: {operation}")
    if isinstance(exc, TransientReadFault):
        return exc
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, BaseException):
        return classify_transport_exception(operation, exc.reason)
    if isinstance(exc, socket.gaierror):
        return TransientReadFault(operation, "DNS_BLIP")
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return TransientReadFault(operation, "TIMEOUT")
    if isinstance(exc, ConnectionResetError) or getattr(exc, "errno", None) in {
        errno.ECONNRESET,
        54,  # macOS ECONNRESET, retained for cross-platform evidence replay.
        104,  # Linux ECONNRESET.
    }:
        return TransientReadFault(operation, "CONNECTION_RESET")
    # Botocore uses dedicated types whose stable names are more portable than
    # importing the optional SDK in this import-safe module.
    if exc.__class__.__module__.startswith("botocore."):
        if exc.__class__.__name__ in {"ReadTimeoutError", "ConnectTimeoutError"}:
            return TransientReadFault(operation, "TIMEOUT")
        if exc.__class__.__name__ == "EndpointConnectionError":
            return TransientReadFault(operation, "DNS_BLIP")
        if exc.__class__.__name__ == "ConnectionClosedError":
            return TransientReadFault(operation, "CONNECTION_RESET")
    return None


def classify_external_read_failure(
    operation: str, *, returncode: int, stdout: bytes, stderr: bytes
) -> TransientReadFault | None:
    """Classify a failed read-only subprocess without exposing its output."""

    if returncode == 0:
        return None
    text = (stdout + b"\n" + stderr).decode(errors="replace").lower()
    if any(marker in text for marker in ("connection reset by peer", "connection reset")):
        return TransientReadFault(operation, "CONNECTION_RESET")
    if any(
        marker in text
        for marker in (
            "timed out",
            "timeout",
            "context deadline exceeded",
            "i/o timeout",
        )
    ):
        return TransientReadFault(operation, "TIMEOUT")
    if any(
        marker in text
        for marker in (
            "temporary failure in name resolution",
            "no such host",
            "name or service not known",
            "could not resolve host",
        )
    ):
        return TransientReadFault(operation, "DNS_BLIP")
    return None


def invoke_transport_read(operation: str, action: Callable[[], T]) -> T:
    """Invoke one external read and type only approved transport exceptions."""

    try:
        return action()
    except Exception as exc:
        fault = classify_transport_exception(operation, exc)
        if fault is None:
            raise
        raise fault from exc


class IdempotentReadRetrier:
    """Shared hard-budget retrier for one stage or command bundle."""

    def __init__(
        self,
        policy: RetryPolicy,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        policy.validate()
        self.policy = policy
        self.sleeper = sleeper
        self.monotonic = monotonic
        self.started = monotonic()

    def run(self, operation: str, label: str, action: Callable[[], T]) -> tuple[T, dict[str, Any]]:
        return self.run_composed((operation,), label, action)

    def run_composed(
        self,
        operations: tuple[str, ...],
        label: str,
        action: Callable[[], T],
    ) -> tuple[T, dict[str, Any]]:
        """Retry a read-only composition while retaining the exact fault type."""

        if not operations or len(set(operations)) != len(operations) or any(
            operation not in READ_OPERATIONS for operation in operations
        ):
            raise ReadRetryConfigurationError("composed read operations are absent or not allowlisted")
        if not label or any(value in label for value in ("http://", "https://", "?")):
            raise ReadRetryConfigurationError("read retry label is absent or may contain a URL")
        events: list[dict[str, Any]] = []
        for attempt in range(1, self.policy.maximum_attempts + 1):
            elapsed = self.monotonic() - self.started
            if elapsed >= self.policy.hard_cap_seconds:
                audit = self._audit(operations, label, attempt - 1, events, "HARD_CAP_EXHAUSTED")
                raise TransientReadRetryExhausted("+".join(operations), audit)
            try:
                value = action()
            except TransientReadFault as exc:
                if exc.operation not in operations:
                    raise ReadRetryConfigurationError("typed transient operation is outside the composition") from exc
                events.append(
                    {
                        "attempt": attempt,
                        "operation": exc.operation,
                        "classification": exc.category,
                        "retryable": True,
                    }
                )
                if attempt == self.policy.maximum_attempts:
                    raise TransientReadRetryExhausted(
                        "+".join(operations),
                        self._audit(operations, label, attempt, events, "ATTEMPTS_EXHAUSTED"),
                    ) from exc
                delay = self.policy.backoff_seconds[attempt - 1]
                if self.monotonic() - self.started + delay >= self.policy.hard_cap_seconds:
                    raise TransientReadRetryExhausted(
                        "+".join(operations),
                        self._audit(operations, label, attempt, events, "HARD_CAP_EXHAUSTED"),
                    ) from exc
                self.sleeper(delay)
                continue
            return value, self._audit(operations, label, attempt, events, "PASS")
        raise AssertionError("unreachable read retry state")

    def _audit(
        self,
        operations: tuple[str, ...],
        label: str,
        attempts: int,
        events: list[dict[str, Any]],
        outcome: str,
    ) -> dict[str, Any]:
        return {
            "status": outcome,
            "operations": list(operations),
            "label": label,
            "attempts": attempts,
            "maximum_attempts": self.policy.maximum_attempts,
            "backoff_seconds": list(self.policy.backoff_seconds),
            "hard_cap_seconds": self.policy.hard_cap_seconds,
            "elapsed_seconds": round(self.monotonic() - self.started, 3),
            "transient_events": events,
            "verification_failures_retryable": False,
            "writes_or_mutations_retryable": False,
            "contains_urls_credentials_model_data_audio_or_phi": False,
        }
