# API reference

Base URL: `http://localhost:8000` locally. Interactive OpenAPI docs are served at `/docs`.

All timestamps are ISO 8601 in UTC. Release lists and event lists are newest first and use cursor pagination. Pass `next_cursor` back as `?cursor=` to get the next page; it is `null` on the last page.

## Contents

- [CI webhooks](#ci-webhooks)
- [Releases](#releases)
- [Services](#services)
- [Events](#events)
- [Summary stats](#summary-stats)
- [Operations](#operations)
- [Release statuses](#release-statuses)

---

## CI webhooks

### `POST /api/webhooks/ci`

This is how CI tells ReleaseWatch about deployments. The request body must be signed.

| Header | Required | Value |
|---|---|---|
| `X-ReleaseWatch-Signature` | yes | `sha256=` + hex HMAC-SHA256 of the raw body, keyed with `RELEASEWATCH_WEBHOOK_SECRET` |
| `X-ReleaseWatch-Delivery` | recommended | A unique id per delivery. A repeated id is accepted but not applied a second time. |

Body:

| Field | Type | Notes |
|---|---|---|
| `event` | string | `deployment.started`, `deployment.succeeded`, `deployment.failed`, `rollback.completed`, `rollback.failed` |
| `service` | string | Lowercase `[a-z0-9._-]`. Services are created the first time they are seen. |
| `environment` | string | Default `production`. |
| `version` | string | |
| `deployment_id` | string? | Links the events of one deployment. If omitted, `environment:version` is used. |
| `commit_sha` | string? | |
| `health_url` | URL? | Probed after `deployment.succeeded`. Falls back to the service's `default_health_url`. |
| `ci_run_url` | URL? | |
| `actor` | string? | |

A deployment can send its fields across several events. For example, `health_url` may only arrive with `deployment.succeeded`; it is merged into the release.

```bash
BODY='{"event":"deployment.succeeded","service":"checkout","version":"v1.4.0","deployment_id":"run-812","health_url":"https://checkout.example.com/healthz"}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$RELEASEWATCH_WEBHOOK_SECRET" | sed 's/^.* //')"

curl -X POST http://localhost:8000/api/webhooks/ci \
  -H "Content-Type: application/json" \
  -H "X-ReleaseWatch-Signature: $SIG" \
  -H "X-ReleaseWatch-Delivery: $(uuidgen)" \
  -d "$BODY"
```

`scripts/send_webhook.py` does the signing for you:

```bash
python scripts/send_webhook.py deployment.succeeded --service checkout --version v1.4.0 \
  --deployment-id run-812 --health-url https://checkout.example.com/healthz
```

Response `202 Accepted`:

```json
{
  "event_id": 4211,
  "release_id": 318,
  "release_status": "verifying",
  "applied": true,
  "duplicate": false,
  "detail": null
}
```

An event is always recorded, even when `applied` is `false`. That happens when the event doesn't fit the release's current state, such as `deployment.succeeded` for a release that already failed; `detail` gives the reason. Error responses:

- `401` if the signature is missing or wrong.
- `422` if the body is invalid. `detail` lists the fields at fault.

### GitHub Actions example

```yaml
- name: Report deploy to ReleaseWatch
  if: always()
  env:
    SECRET: ${{ secrets.RELEASEWATCH_WEBHOOK_SECRET }}
    EVENT: ${{ job.status == 'success' && 'deployment.succeeded' || 'deployment.failed' }}
  run: |
    BODY=$(jq -nc --arg e "$EVENT" --arg v "${GITHUB_SHA::7}" --arg id "$GITHUB_RUN_ID" \
      --arg run "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID" \
      '{event:$e, service:"checkout", version:$v, deployment_id:$id, commit_sha:env.GITHUB_SHA,
        ci_run_url:$run, actor:env.GITHUB_ACTOR, health_url:"https://checkout.example.com/healthz"}')
    SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')"
    curl --fail -X POST https://releasewatch.example.com/api/webhooks/ci \
      -H "Content-Type: application/json" -H "X-ReleaseWatch-Signature: $SIG" \
      -H "X-ReleaseWatch-Delivery: $GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT-$EVENT" -d "$BODY"
```

---

## Releases

### `GET /api/releases`

| Query | Notes |
|---|---|
| `service` | Service name |
| `environment` | |
| `status` | One of the [release statuses](#release-statuses) |
| `limit` | 1–100, default 25 |
| `cursor` | `next_cursor` from the previous page |

```bash
curl "http://localhost:8000/api/releases?service=checkout&status=unhealthy&limit=2"
```

```json
{
  "items": [
    {
      "id": 318,
      "service": "checkout",
      "environment": "production",
      "version": "v1.4.0",
      "commit_sha": "3f9c2e1a7b",
      "status": "unhealthy",
      "health_url": "https://checkout.example.com/healthz",
      "ci_run_url": "https://github.com/acme/checkout/actions/runs/812",
      "triggered_by": "anto",
      "created_at": "2026-10-11T05:02:11.204Z",
      "deployed_at": "2026-10-11T05:04:40.017Z",
      "verified_at": "2026-10-11T05:05:52.911Z",
      "rollback_target_id": null,
      "rollback_reason": null,
      "rollback_requested_at": null,
      "rollback_completed_at": null,
      "health": { "passed": 1, "failed": 2, "required": 3 },
      "event_count": 4
    }
  ],
  "next_cursor": "WyIyMDI2LTEwLTExVDA1OjAyOjExLjIwNCswMDowMCIsMzE4XQ"
}
```

An invalid cursor returns `400`.

### `POST /api/releases`

Registers a deployment that didn't come through CI. Verification starts right away.

```bash
curl -X POST http://localhost:8000/api/releases \
  -H "Content-Type: application/json" \
  -d '{"service":"checkout","environment":"staging","version":"v1.5.0-rc1",
       "commit_sha":"9f8e7d6","health_url":"https://staging.checkout.example.com/healthz",
       "triggered_by":"anto"}'
```

Returns `201` with the release. Its status is `verifying`, or `healthy` when no health URL is available.

### `GET /api/releases/{id}`

Returns the release plus `rollback_target_version`, `health_checks` (in sequence order) and `events` (the timeline, oldest first):

```json
{
  "id": 318,
  "status": "rolling_back",
  "rollback_target_id": 301,
  "rollback_target_version": "v1.3.2",
  "health_checks": [
    {
      "id": 911,
      "sequence": 1,
      "outcome": "failed",
      "http_status": 503,
      "latency_ms": 41,
      "error": "HTTP 503",
      "checked_at": "2026-10-11T05:04:50.112Z"
    }
  ],
  "events": [
    {
      "id": 4209,
      "event_type": "deployment.started",
      "source": "ci",
      "payload": { "actor": "anto" },
      "received_at": "2026-10-11T05:02:11.204Z"
    },
    {
      "id": 4214,
      "event_type": "rollback.requested",
      "source": "worker",
      "payload": {
        "reason": "Automatic: 2 of 2 health checks failed",
        "target_version": "v1.3.2"
      },
      "received_at": "2026-10-11T05:05:52.913Z"
    }
  ]
}
```

The example above is abbreviated. Returns `404` for an unknown id.

### `POST /api/releases/{id}/rollback`

```bash
curl -X POST http://localhost:8000/api/releases/318/rollback \
  -H "Content-Type: application/json" -d '{"reason":"error rate spike"}'
```

- Moves the release to `rolling_back` and targets the newest earlier `healthy` release of the same service and environment.
- If the service has a `rollback_webhook_url`, the worker POSTs the request there, retrying with backoff.
- CI finishes the rollback by sending `rollback.completed` (status becomes `rolled_back`) or `rollback.failed` (status goes back to `unhealthy`).
- Returns `409` if the release is `deploying`, already `rolling_back`, or already `rolled_back`.

Payload sent to `rollback_webhook_url`:

```json
{
  "event": "rollback.requested",
  "service": "checkout",
  "environment": "production",
  "release_id": 318,
  "version": "v1.4.0",
  "target_version": "v1.3.2",
  "target_commit_sha": "c0ffee1",
  "reason": "error rate spike"
}
```

---

## Services

| Method | Path | |
|---|---|---|
| `GET` | `/api/services` | List services |
| `GET` | `/api/services/{name}` | Get one service |
| `PUT` | `/api/services/{name}` | Create or update settings |

```bash
curl -X PUT http://localhost:8000/api/services/checkout \
  -H "Content-Type: application/json" \
  -d '{"name":"checkout","repository":"acme/checkout",
       "default_health_url":"https://checkout.example.com/healthz",
       "auto_rollback":true,
       "rollback_webhook_url":"https://ci.example.com/hooks/rollback"}'
```

With `auto_rollback` on, a release that fails verification is moved to `rolling_back` automatically.

---

## Events

### `GET /api/events`

The raw event log. Filter with `service`, `release_id` or `event_type`. `limit` is 1–200 (default 50) and `cursor` works as for releases.

```bash
curl "http://localhost:8000/api/events?service=checkout&event_type=verification.failed"
```

| Event type | Source | Meaning |
|---|---|---|
| `deployment.started` / `.succeeded` / `.failed` | ci | Deployment progress from CI |
| `release.created` | api | Release registered through the API or UI |
| `verification.started` / `.passed` / `.failed` / `.skipped` | ci, api, worker | Post-deployment health verification |
| `rollback.requested` | api, worker | Manual or automatic rollback request |
| `rollback.completed` / `.failed` | ci | Rollback result from CI |

---

## Summary stats

### `GET /api/stats/summary?window_days=7&environment=production`

```json
{
  "window_days": 7,
  "releases": 1014,
  "by_status": {
    "deploying": 9,
    "verifying": 12,
    "healthy": 806,
    "unhealthy": 63,
    "failed": 61,
    "rolling_back": 21,
    "rolled_back": 42
  },
  "change_failure_rate": 0.1854,
  "services": [
    {
      "service": "checkout",
      "releases": 27,
      "failed": 4,
      "change_failure_rate": 0.1481,
      "last_release_status": "rolled_back",
      "last_release_version": "v4.2.0",
      "last_release_at": "2026-10-11T05:24:02Z"
    }
  ]
}
```

The change failure rate is the share of releases that ended `failed`, `unhealthy`, `rolling_back` or `rolled_back`.

---

## Operations

| Path | Purpose |
|---|---|
| `GET /healthz` | Liveness. Returns `{"status":"ok","version":"0.1.0","commit":"<sha>"}`. |
| `GET /readyz` | Readiness. Returns `503` if the database is unreachable or not migrated. |

## Release statuses

```
deploying ──succeeded──▶ verifying ──checks pass──▶ healthy
    │                        │
    └──failed──▶ failed      └──checks fail──▶ unhealthy

{verifying, healthy, unhealthy, failed} ──rollback──▶ rolling_back ──completed──▶ rolled_back
                                                           └──────failed──────▶ unhealthy
```
