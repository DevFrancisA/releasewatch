#!/usr/bin/env python3
"""Post-deploy smoke checks for a running ReleaseWatch (standard library only).

Waits for the service to come up (optionally on a specific commit), then checks
that the API, database, UI, and webhook signature verification all work. With
--webhook-secret it also sends a signed deployment through the full ingest path
using a dedicated `releasewatch-smoke` service.

    python scripts/smoke.py https://releasewatch.example.com \
        --expect-commit "$GITHUB_SHA" --webhook-secret "$SECRET"

Exits non-zero on the first failed check.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any


class SmokeFailure(Exception):
    pass


class Client:
    def __init__(self, base_url: str, timeout: float = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def call(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        request = urllib.request.Request(
            self.base_url + path, data=body, method=method, headers=headers or {}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return (
                    response.status,
                    response.headers.get("content-type", ""),
                    response.read().decode(),
                )
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("content-type", ""), exc.read().decode()

    def json(self, method: str, path: str, expect: int = 200, **kwargs: Any) -> Any:
        status, _, text = self.call(method, path, **kwargs)
        if status != expect:
            raise SmokeFailure(f"{method} {path}: expected {expect}, got {status}: {text[:300]}")
        return json.loads(text)


def check(name: str):
    def decorator(fn):
        def run(*args, **kwargs):
            started = time.perf_counter()
            fn(*args, **kwargs)
            print(f"  ok  {name} ({(time.perf_counter() - started) * 1000:.0f} ms)")

        return run

    return decorator


def wait_until_live(client: Client, expect_commit: str | None, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last = "no response"
    while time.monotonic() < deadline:
        try:
            status, _, text = client.call("GET", "/healthz")
            if status == 200:
                commit = json.loads(text).get("commit")
                if not expect_commit or (commit and commit.startswith(expect_commit[:12])):
                    print(f"  ok  live (commit {commit or 'unknown'})")
                    return
                last = f"serving commit {commit}, waiting for {expect_commit[:12]}"
            else:
                last = f"HTTP {status}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = str(exc)
        time.sleep(5)
    raise SmokeFailure(f"service not live after {timeout:.0f}s: {last}")


@check("database ready")
def check_ready(client: Client) -> None:
    client.json("GET", "/readyz")


@check("UI served")
def check_ui(client: Client) -> None:
    status, content_type, text = client.call("GET", "/releases")
    if status != 200 or "text/html" not in content_type or 'id="root"' not in text:
        raise SmokeFailure(f"GET /releases did not return the app shell (HTTP {status})")


@check("read endpoints")
def check_reads(client: Client) -> None:
    page = client.json("GET", "/api/releases?limit=5")
    if not isinstance(page.get("items"), list) or "next_cursor" not in page:
        raise SmokeFailure("unexpected /api/releases shape")
    summary = client.json("GET", "/api/stats/summary")
    if "change_failure_rate" not in summary:
        raise SmokeFailure("unexpected /api/stats/summary shape")
    client.json("GET", "/api/services")


@check("unsigned webhooks rejected")
def check_signature_required(client: Client) -> None:
    client.json(
        "POST",
        "/api/webhooks/ci",
        expect=401,
        body=b'{"event":"deployment.started","service":"x","version":"1"}',
        headers={"Content-Type": "application/json", "X-ReleaseWatch-Signature": "sha256=00"},
    )


@check("signed webhook round trip")
def check_webhook_round_trip(client: Client, secret: str) -> None:
    version = time.strftime("smoke-%Y%m%d-%H%M%S")
    delivery = str(uuid.uuid4())

    def send(event: str) -> dict[str, Any]:
        body = json.dumps(
            {
                "event": event,
                "service": "releasewatch-smoke",
                "environment": "smoke",
                "version": version,
                "deployment_id": version,
                "actor": "smoke-check",
            }
        ).encode()
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return client.json(
            "POST",
            "/api/webhooks/ci",
            expect=202,
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-ReleaseWatch-Signature": signature,
                "X-ReleaseWatch-Delivery": f"{delivery}-{event}",
            },
        )

    send("deployment.started")
    result = send("deployment.succeeded")
    if result["release_status"] != "healthy":
        raise SmokeFailure(f"expected healthy release, got {result}")
    detail = client.json("GET", f"/api/releases/{result['release_id']}")
    events = [e["event_type"] for e in detail["events"]]
    if events[:2] != ["deployment.started", "deployment.succeeded"]:
        raise SmokeFailure(f"unexpected timeline {events}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--expect-commit", help="wait until /healthz reports this commit")
    parser.add_argument("--webhook-secret", help="also send a signed webhook end to end")
    parser.add_argument("--wait", type=float, default=300, help="seconds to wait for the deploy")
    args = parser.parse_args()

    client = Client(args.url)
    print(f"smoke checks against {client.base_url}")
    try:
        wait_until_live(client, args.expect_commit, args.wait)
        check_ready(client)
        check_ui(client)
        check_reads(client)
        check_signature_required(client)
        if args.webhook_secret:
            check_webhook_round_trip(client, args.webhook_secret)
    except SmokeFailure as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print("all smoke checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
