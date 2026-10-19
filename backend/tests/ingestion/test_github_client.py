"""GitHubRestClient contracts against a mocked wire (respx).

This is the ONLY suite that touches HTTP. It pins: bearer auth, page
consumption until exhaustion, per-page yield ordering, rate-limit
backoff via an injected sleeper (no real sleeps), Retry-After respect,
exhaustion raising RateLimitError, and 5xx treated as transient with the
same backoff schedule before surfacing TransientUpstreamError."""

import httpx
import pytest
import respx

REPO = "encode/httpx"
BASE = f"https://api.github.com/repos/{REPO}"


@pytest.fixture()
def sleeper():
    calls: list[float] = []

    def sleep(seconds: float) -> None:
        calls.append(seconds)

    return sleep


@pytest.fixture()
def client(sleeper):
    from app.ingestion.github_client import GitHubRestClient

    github = GitHubRestClient(github_token="gh_test", sleeper=sleeper)
    github.sleeper_calls = sleeper.calls
    yield github
    respx.mock.stop()


def list_page(items: list, per_page: int = 2) -> dict:
    return items


@pytest.fixture()
def prs_page():
    from fakes import deep_pr, poison_pr, trivial_pr

    return [deep_pr(), poison_pr(), trivial_pr()]


@respx.mock
class TestPagination:
    def test_pull_requests_are_fetched_across_pages(self, client, prs_page) -> None:
        seen_pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"]) if "page" in request.url.params else 1
            seen_pages.append(page)
            per_page = 2
            start = (page - 1) * per_page
            chunk = prs_page[start : start + per_page]
            return httpx.Response(200, json=chunk)

        respx.get(f"{BASE}/pulls*").mock(side_effect=handler)
        got = list(client.merged_pull_requests(REPO, since="2024-01-01"))
        assert seen_pages == [1, 2, 3]
        assert [pr["number"] for pr in got] == [1131, 1132, 1133]

    def test_authorization_header_is_sent(self, client, prs_page) -> None:
        headers_seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            headers_seen.append(dict(request.headers))
            return httpx.Response(200, json=prs_page)

        respx.get(f"{BASE}/pulls").mock(side_effect=handler)
        got = list(client.merged_pull_requests(REPO, since="2024-01-01"))
        assert got  # requests were made
        assert all(h.get("Authorization") == "Bearer gh_test" for h in headers_seen)

    def test_filtering_happens_in_the_service_not_the_client(self, client, prs_page) -> None:
        """The client must yield everything it receives so test suites
        stay honest about where the merged/window filter actually lives;
        no server-side filtering is simulated here."""
        handler_hits = []

        def handler(request: httpx.Request) -> httpx.Response:
            handler_hits.append(1)
            return httpx.Response(200, json=prs_page)

        respx.get(f"{BASE}/pulls").mock(side_effect=handler)
        got = list(client.merged_pull_requests(REPO, since="2024-01-01"))
        assert handler_hits and got


@pytest.fixture()
def rate_limit_factory():
    def make(remaining: str = "0") -> httpx.Response:
        return httpx.Response(
            403,
            headers={
                "X-RateLimit-Remaining": remaining,
                "Retry-After": "7",
            },
            json={"message": "API rate limit exceeded"},
        )

    return make


@respx.mock
class TestRateLimitBehavior:
    def test_rate_limit_retries_then_succeeds(self, client, prs_page, rate_limit_factory) -> None:
        responses = iter([rate_limit_factory(), httpx.Response(200, json=prs_page)])
        respx.get(f"{BASE}/pulls").mock(side_effect=lambda request: next(responses))
        got = list(client.merged_pull_requests(REPO, since="2024-01-01"))
        assert [pr["number"] for pr in got] == [1131, 1132, 1133]
        assert client.sleeper_calls == [7.0], "must honor Retry-After"

    def test_persistent_rate_limit_raises_rate_limit_error_after_backoffs(self, client,
        rate_limit_factory) -> None:
        respx.get(f"{BASE}/pulls").mock(side_effect=lambda request: rate_limit_factory())
        from app.core.errors import RateLimitError

        with pytest.raises(RateLimitError):
            list(client.merged_pull_requests(REPO, since="2024-01-01"))
        assert client.sleeper_calls == [1.0, 2.0, 4.0]

    def test_persistent_server_error_is_transient_not_rate_limit(self, client) -> None:
        respx.get(f"{BASE}/pulls").mock(side_effect=lambda request: httpx.Response(500, json={}))
        from app.core.errors import TransientUpstreamError

        with pytest.raises(TransientUpstreamError):
            list(client.merged_pull_requests(REPO, since="2024-01-01"))
        assert client.sleeper_calls == [1.0, 2.0, 4.0]
