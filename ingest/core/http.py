"""HTTP client with retry, backoff and structured logging.

Design notes, because each of these is a decision rather than a default:

* Every request carries an explicit timeout. `requests` has no default, so a
  hung connection blocks the extractor forever. This is the single most common
  way a scheduled pipeline silently stops.
* Retries cover transient failures only. A 404 or a 401 will not succeed on
  the second attempt, and retrying them wastes the rate-limit budget that the
  transient cases need.
* Backoff is exponential with jitter. Without jitter, concurrent extractors
  retry in lockstep and hit the origin in a thundering herd.
* `Retry-After` wins over computed backoff when the server sends it. The
  server knows when it will be ready and we do not.
* One Session per client, so connections pool across a paginated crawl.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Callable, Mapping

import requests
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ingest.core.log import get_logger

log = get_logger(__name__)

#: Status codes worth a second attempt. 408 request timeout, 425 too early,
#: 429 rate limited, and the 5xx family that indicates an unhealthy origin.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

DEFAULT_USER_AGENT = (
    "pah-platform/0.1 (portfolio analytics project; +https://github.com/"
    "LibertyKanojerera/pah-platform-snowflake-dbt-dagster)"
)


class TransientHTTPError(Exception):
    """A failure worth retrying."""

    def __init__(
        self, *, url: str, status_code: int, retry_after: float | None = None
    ) -> None:
        super().__init__(f"{status_code} from {url}")
        self.url = url
        self.status_code = status_code
        self.retry_after = retry_after


class PermanentHTTPError(Exception):
    """A failure that will not improve on retry."""

    def __init__(self, *, url: str, status_code: int, body: bytes) -> None:
        super().__init__(f"{status_code} from {url}")
        self.url = url
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class Response:
    """What the extractors see.

    Deliberately not a `requests.Response`: the source modules should not be
    coupled to the HTTP library, which keeps them testable with plain fixtures.
    """

    url: str
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    elapsed_s: float
    attempts: int

    def json(self) -> Any:
        return json.loads(self.body)


def _parse_retry_after(value: str | None) -> float | None:
    """`Retry-After` as seconds. The HTTP-date form is ignored deliberately:
    these APIs send the delta-seconds form, and a wrong parse is worse than
    falling back to computed backoff."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _wait_strategy(
    base: Callable[[RetryCallState], float],
    *,
    cap_s: float,
) -> Callable[[RetryCallState], float]:
    def _wait(state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        if isinstance(exc, TransientHTTPError) and exc.retry_after is not None:
            return min(exc.retry_after, cap_s)
        return base(state)

    return _wait


class HttpClient:
    """One client per source. Use as a context manager."""

    def __init__(
        self,
        *,
        source: str,
        base_url: str = "",
        timeout: tuple[float, float] = (5.0, 30.0),
        max_attempts: int = 5,
        user_agent: str = DEFAULT_USER_AGENT,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.source = source
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max_attempts

        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": user_agent, "Accept": "application/json"}
        )
        if headers:
            self._session.headers.update(dict(headers))

        self._log = log.bind(source=source)

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Response:
        url = f"{self.base_url}/{path.lstrip('/')}" if self.base_url else path

        retrying = Retrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=_wait_strategy(
                wait_exponential_jitter(initial=1.0, max=30.0), cap_s=60.0
            ),
            retry=retry_if_exception_type(
                (
                    TransientHTTPError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                )
            ),
            reraise=True,
            before_sleep=self._log_retry,
        )

        for attempt in retrying:
            with attempt:
                return self._attempt(url, params, attempt.retry_state.attempt_number)

        raise RuntimeError("unreachable: Retrying exhausted without raising")

    def _attempt(
        self, url: str, params: Mapping[str, Any] | None, attempt_number: int
    ) -> Response:
        raw = self._session.get(url, params=params, timeout=self.timeout)
        elapsed = raw.elapsed.total_seconds()

        if raw.status_code in RETRYABLE_STATUS:
            raise TransientHTTPError(
                url=url,
                status_code=raw.status_code,
                retry_after=_parse_retry_after(raw.headers.get("Retry-After")),
            )

        if raw.status_code >= 400:
            self._log.error("http.permanent_failure", url=url, status=raw.status_code)
            raise PermanentHTTPError(
                url=url, status_code=raw.status_code, body=raw.content
            )

        self._log.debug(
            "http.ok",
            url=url,
            status=raw.status_code,
            elapsed_s=round(elapsed, 3),
            bytes=len(raw.content),
            attempts=attempt_number,
        )

        return Response(
            url=raw.url,
            status_code=raw.status_code,
            headers=dict(raw.headers),
            body=raw.content,
            elapsed_s=elapsed,
            attempts=attempt_number,
        )

    def _log_retry(self, state: RetryCallState) -> None:
        exc = state.outcome.exception() if state.outcome else None
        self._log.warning(
            "http.retry",
            attempt=state.attempt_number,
            sleeping_s=round(state.next_action.sleep, 2) if state.next_action else None,
            error=type(exc).__name__ if exc else None,
            status=getattr(exc, "status_code", None),
        )
