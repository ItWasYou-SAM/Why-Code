"""GitHub REST client: pagination, auth, rate-limit backoff.

This is the only module in the project that speaks HTTP to GitHub. The
sleeper is injectable so backoff is testable without real waiting, and
every request is counted so the run report can state real API usage.

Pagination follows ``Link: rel="next"`` when GitHub sends it. Page-number
increments remain as a fallback for servers (and test doubles) that omit
the header.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from app.core.errors import RateLimitError, TransientUpstreamError

API_ROOT = "https://api.github.com"
PER_PAGE = 100
BACKOFF_SCHEDULE = (1.0, 2.0, 4.0)


class GitHubRestClient:
    def __init__(
        self,
        github_token: str,
        *,
        client: httpx.Client | None = None,
        sleeper: Any = None,
        api_root: str = API_ROOT,
    ) -> None:
        self.api_root = api_root.rstrip("/")
        self._client = client or httpx.Client(
            base_url=self.api_root,
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )
        self.sleeper = sleeper or _real_sleep
        self._total_requests = 0
        self.rate_limit_remaining: int | None = None

    def close(self) -> None:
        self._client.close()

    def total_calls(self) -> int:
        """Every wire attempt, including retries. This is what a report
        must count: a retried 502 is still quota spent."""
        return self._total_requests

    # -- pagination -----------------------------------------------------
    def _paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[Any]:
        page = 1
        next_path = path
        next_params: dict[str, Any] | None = {
            "per_page": PER_PAGE,
            "page": page,
            **(params or {}),
        }
        while True:
            response = self._request_raw("GET", next_path, params=next_params)
            payload = response.json()
            if not isinstance(payload, list) or not payload:
                return
            yield from payload

            next_url = _next_link(response)
            if next_url:
                # The next URL already carries per_page/page, so sending
                # params again would duplicate (and can override) them.
                next_path, next_params = next_url, None
                continue
            if len(payload) < PER_PAGE:
                return
            page += 1
            next_path, next_params = path, {
                "per_page": PER_PAGE,
                "page": page,
                **(params or {}),
            }

    # -- transport ------------------------------------------------------
    def _request_raw(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        endpoint = _endpoint_label(path)
        last_rate_error: httpx.Response | None = None
        last_transient: httpx.Response | Exception | None = None
        for delay in (*BACKOFF_SCHEDULE, None):
            self._total_requests += 1
            try:
                response = self._client.request(method, path, params=params)
            except httpx.HTTPError as error:
                last_transient = error
                if delay is None:
                    break
                self.sleeper(delay)
                continue

            self._record_rate_limit(response)

            if response.status_code == 403 or response.status_code == 429:
                if _is_rate_limited(response):
                    last_rate_error = response
                    if delay is None:
                        break
                    wait = _retry_after(response)
                    self.sleeper(wait if wait is not None else delay)
                    continue
                raise RateLimitError(
                    f"GitHub denied {endpoint}: {response.text[:200]}",
                    component="ingestion",
                    endpoint=endpoint,
                )
            if response.status_code >= 500:
                last_transient = response
                if delay is None:
                    break
                self.sleeper(delay)
                continue
            if response.status_code >= 400:
                raise TransientUpstreamError(
                    f"GitHub {response.status_code} for {endpoint}: {response.text[:200]}",
                    component="ingestion",
                    endpoint=endpoint,
                )
            return response

        if last_rate_error is not None:
            raise RateLimitError(
                f"rate limit exhausted for {endpoint}",
                component="ingestion",
                endpoint=endpoint,
            )
        raise TransientUpstreamError(
            f"GitHub request failed for {endpoint}: {last_transient}",
            component="ingestion",
            endpoint=endpoint,
        )

    def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        return self._request_raw(method, path, params=params).json()

    def _record_rate_limit(self, response: httpx.Response) -> None:
        value = response.headers.get("X-RateLimit-Remaining")
        if value is None:
            return
        try:
            self.rate_limit_remaining = int(value)
        except ValueError:
            return

    # -- endpoints ------------------------------------------------------
    def merged_pull_requests(
        self, repo: str, since: str, until: str | None = None
    ) -> Iterator[dict[str, Any]]:
        params = {
            "state": "closed",
            "sort": "updated",
            "direction": "desc",
            "since": since,
        }
        if until:
            params["until"] = until
        yield from self._paginate(f"/repos/{repo}/pulls", params)

    def pr_detail(self, repo: str, number: int) -> dict[str, Any]:
        payload: dict[str, Any] = self._request("GET", f"/repos/{repo}/pulls/{number}")
        return payload

    def pr_files(self, repo: str, number: int) -> list[dict[str, Any]]:
        return list(self._paginate(f"/repos/{repo}/pulls/{number}/files"))

    def pr_commits(self, repo: str, number: int) -> list[dict[str, Any]]:
        return list(self._paginate(f"/repos/{repo}/pulls/{number}/commits"))

    def pr_reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        return list(self._paginate(f"/repos/{repo}/pulls/{number}/reviews"))

    def pr_review_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return list(self._paginate(f"/repos/{repo}/pulls/{number}/comments"))

    def issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return list(self._paginate(f"/repos/{repo}/issues/{number}/comments"))

    def linked_issues(self, repo: str, number: int) -> list[dict[str, Any]]:
        import re

        pr = self.pr_detail(repo, number)
        body = str(pr.get("body") or "")
        numbers = sorted(
            {
                int(match)
                for match in re.findall(
                    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)",
                    body,
                    flags=re.IGNORECASE,
                )
            }
        )
        return [
            self._request("GET", f"/repos/{repo}/issues/{issue_number}")
            for issue_number in numbers
        ]

    def blob(self, repo: str, file_sha: str) -> str:
        import base64

        payload = self._request("GET", f"/repos/{repo}/git/blobs/{file_sha}")
        content = payload.get("content", "")
        encoding = payload.get("encoding", "base64")
        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8")
        return str(content)


def _endpoint_label(path: str) -> str:
    """Report-shaped endpoint: ``pulls/1131/files``, not a full URL.

    Link-following passes absolute URLs, so strip scheme, host, query and
    the ``owner/repo`` prefix before writing the failure into a report.
    """
    from urllib.parse import urlsplit

    clean = urlsplit(path).path
    marker = "/repos/"
    if marker in clean:
        tail = clean.split(marker, 1)[1]
        parts = tail.split("/", 2)
        return parts[2] if len(parts) == 3 else tail
    return clean.lstrip("/")


def _next_link(response: httpx.Response) -> str | None:
    """Return the ``rel="next"`` URL from a GitHub Link header, if any."""
    header = response.headers.get("Link")
    if not header:
        return None
    for part in header.split(","):
        segments = [segment.strip() for segment in part.split(";")]
        if not segments or not (segments[0].startswith("<") and segments[0].endswith(">")):
            continue
        relations = {
            segment.split("=", 1)[1].strip().strip('"')
            for segment in segments[1:]
            if "=" in segment and segment.split("=", 1)[0].strip() == "rel"
        }
        if "next" in relations:
            return str(segments[0][1:-1])
    return None


def _is_rate_limited(response: httpx.Response) -> bool:
    if response.headers.get("X-RateLimit-Remaining") == "0":
        return True
    return "rate limit" in response.text.lower()


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _real_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
