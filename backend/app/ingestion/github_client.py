"""GitHub REST client: pagination, auth, rate-limit backoff.

This is the only module in the project that speaks HTTP to GitHub. The
sleeper is injectable so backoff is testable without real waiting.
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

    def close(self) -> None:
        self._client.close()

    # -- pagination -----------------------------------------------------
    def _paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[Any]:
        page = 1
        while True:
            query = {"per_page": PER_PAGE, "page": page, **(params or {})}
            payload = self._request("GET", path, params=query)
            if not isinstance(payload, list) or not payload:
                return
            yield from payload
            if len(payload) < PER_PAGE:
                return
            page += 1

    def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        last_rate_error: httpx.Response | None = None
        last_transient: httpx.Response | Exception | None = None
        for delay in (*BACKOFF_SCHEDULE, None):
            try:
                response = self._client.request(method, path, params=params)
            except httpx.HTTPError as error:
                last_transient = error
                if delay is None:
                    break
                self.sleeper(delay)
                continue

            if response.status_code == 403 or response.status_code == 429:
                if _is_rate_limited(response):
                    last_rate_error = response
                    if delay is None:
                        break
                    wait = _retry_after(response)
                    self.sleeper(wait if wait is not None else delay)
                    continue
                raise RateLimitError(
                    f"GitHub denied {path}: {response.text[:200]}",
                    component="ingestion",
                )
            if response.status_code >= 500:
                last_transient = response
                if delay is None:
                    break
                self.sleeper(delay)
                continue
            if response.status_code >= 400:
                raise TransientUpstreamError(
                    f"GitHub {response.status_code} for {path}: {response.text[:200]}",
                    component="ingestion",
                )
            return response.json()

        if last_rate_error is not None:
            raise RateLimitError(
                f"rate limit exhausted for {path}", component="ingestion"
            )
        raise TransientUpstreamError(
            f"GitHub request failed for {path}: {last_transient}", component="ingestion"
        )

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
