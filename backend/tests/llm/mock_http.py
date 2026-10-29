import json
from collections.abc import Callable
from typing import Any

import httpx2

Reply = httpx2.Response | Callable[[httpx2.Request], httpx2.Response]


class MockHTTP:
    """Scripted HTTP transport for adapter tests.

    Hands out an httpx2.Client backed by MockTransport, so requests can never reach the
    network. Each request consumes the next scripted reply; running out fails the test.
    """

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")
        reply = self.replies.pop(0)
        return reply(request) if callable(reply) else reply

    @property
    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self))

    @property
    def last(self) -> httpx2.Request:
        return self.requests[-1]

    def body(self, index: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


def json_reply(status: int, body: Any, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(status, json=body, headers=headers)


def raise_timeout(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ReadTimeout("timed out", request=request)


def raise_connect_error(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("connection refused", request=request)
