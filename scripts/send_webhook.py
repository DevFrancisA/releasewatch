#!/usr/bin/env python3
"""Send a signed CI webhook to ReleaseWatch (standard library only).

Examples:
    python scripts/send_webhook.py deployment.started --service checkout --version v1.4.0
    python scripts/send_webhook.py deployment.succeeded --service checkout --version v1.4.0 \
        --health-url http://api:8000/healthz
    python scripts/send_webhook.py rollback.completed --service checkout --version v1.4.0
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
import uuid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "event",
        choices=[
            "deployment.started",
            "deployment.succeeded",
            "deployment.failed",
            "rollback.completed",
            "rollback.failed",
        ],
    )
    parser.add_argument("--service", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--environment", default="production")
    parser.add_argument("--deployment-id")
    parser.add_argument("--commit-sha")
    parser.add_argument("--health-url")
    parser.add_argument("--ci-run-url")
    parser.add_argument("--actor")
    parser.add_argument("--url", default=os.environ.get("RELEASEWATCH_URL", "http://localhost:8000"))
    parser.add_argument(
        "--secret", default=os.environ.get("RELEASEWATCH_WEBHOOK_SECRET", "dev-webhook-secret")
    )
    args = parser.parse_args()

    payload = {
        "event": args.event,
        "service": args.service,
        "environment": args.environment,
        "version": args.version,
        "deployment_id": args.deployment_id,
        "commit_sha": args.commit_sha,
        "health_url": args.health_url,
        "ci_run_url": args.ci_run_url,
        "actor": args.actor,
    }
    body = json.dumps({k: v for k, v in payload.items() if v is not None}).encode()
    signature = "sha256=" + hmac.new(args.secret.encode(), body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        args.url.rstrip("/") + "/api/webhooks/ci",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-ReleaseWatch-Signature": signature,
            "X-ReleaseWatch-Delivery": str(uuid.uuid4()),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            print(response.status, response.read().decode())
            return 0
    except urllib.error.HTTPError as exc:
        print(exc.code, exc.read().decode(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
