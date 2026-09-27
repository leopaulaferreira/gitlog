"""Isolate credentials and prevent accidental real HTTP in every unit test."""

import httpx
import pytest


@pytest.fixture(autouse=True)
def isolate_github(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-not-a-real-secret")

    def deny_network(*args: object, **kwargs: object) -> None:
        pytest.fail("Real HTTP is forbidden in unit tests; use MockTransport.")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny_network)
