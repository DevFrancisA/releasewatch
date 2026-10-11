import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  CAN_ROLL_BACK,
  IN_PROGRESS,
  type HealthCheck,
  type ReleaseDetail as Detail,
  type ReleaseEvent,
} from "../api";
import {
  Card,
  ErrorNote,
  HealthMeter,
  Loading,
  shortSha,
  StatusBadge,
  Time,
} from "../components/common";
import { useResource } from "../hooks";

export function ReleaseDetail() {
  const { id = "" } = useParams();
  const release = useResource(() => api.release(id), id, {
    pollMs: 2_000,
    shouldPoll: (r) => IN_PROGRESS.has(r.status),
  });

  if (release.error) return <ErrorNote error={release.error} />;
  if (!release.data) return <Loading />;
  const r = release.data;

  return (
    <>
      <nav className="crumbs">
        <Link to="/releases">Releases</Link> /{" "}
        <Link to={`/releases?service=${encodeURIComponent(r.service)}`}>{r.service}</Link>
      </nav>
      <div className="page-header">
        <h1>
          {r.service} <span className="mono">{r.version}</span>{" "}
          <span className="env">{r.environment}</span>
        </h1>
        <StatusBadge status={r.status} />
      </div>

      <div className="grid-2">
        <Card title="Deployment">
          <dl className="facts">
            <dt>Commit</dt>
            <dd className="mono">{shortSha(r.commit_sha)}</dd>
            <dt>Triggered by</dt>
            <dd>{r.triggered_by ?? "—"}</dd>
            <dt>Created</dt>
            <dd>
              <Time iso={r.created_at} />
            </dd>
            <dt>Deployed</dt>
            <dd>
              <Time iso={r.deployed_at} />
            </dd>
            <dt>Verified</dt>
            <dd>
              <Time iso={r.verified_at} />
            </dd>
            <dt>CI run</dt>
            <dd>
              {r.ci_run_url ? (
                <a href={r.ci_run_url} target="_blank" rel="noreferrer">
                  Open ↗
                </a>
              ) : (
                "—"
              )}
            </dd>
          </dl>
        </Card>

        <HealthChecks release={r} />
      </div>

      <RollbackPanel release={r} onChanged={release.reload} />

      <Card title={`Timeline (${r.events.length})`}>
        <Timeline events={r.events} />
      </Card>
    </>
  );
}

function HealthChecks({ release: r }: { release: Detail }) {
  return (
    <Card
      title="Health checks"
      actions={
        <span data-testid="health-summary">
          {r.health.passed} passed · {r.health.failed} failed · {r.health.required} required
        </span>
      }
    >
      {r.health_url ? (
        <p className="muted small">
          Probing <span className="mono">{r.health_url}</span>
        </p>
      ) : (
        <p className="muted small">No health URL configured; verification skipped.</p>
      )}
      <HealthMeter health={r.health} />
      {r.health_checks.length > 0 ? (
        <ol className="checks" data-testid="health-checks">
          {r.health_checks.map((c) => (
            <CheckRow key={c.id} check={c} />
          ))}
        </ol>
      ) : (
        r.status === "verifying" && <p className="muted small">Waiting for the first check…</p>
      )}
    </Card>
  );
}

function CheckRow({ check: c }: { check: HealthCheck }) {
  return (
    <li className={`check check-${c.outcome}`}>
      <span className="check-seq">#{c.sequence}</span>
      <span className="check-outcome">{c.outcome}</span>
      <span className="mono">{c.http_status ?? "no response"}</span>
      <span className="muted">{c.latency_ms !== null ? `${c.latency_ms} ms` : ""}</span>
      {c.error && <span className="check-error">{c.error}</span>}
      <Time iso={c.checked_at} />
    </li>
  );
}

function RollbackPanel({ release: r, onChanged }: { release: Detail; onChanged: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<Error>();

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(undefined);
    try {
      await api.rollback(r.id, reason.trim());
      setOpen(false);
      setReason("");
      await onChanged();
    } catch (err) {
      setError(err as Error);
    } finally {
      setSubmitting(false);
    }
  }

  if (r.rollback_requested_at) {
    return (
      <Card title="Rollback">
        <div className="rollback-status" data-testid="rollback-status">
          <StatusBadge status={r.status} />
          <span>
            {r.status === "rolled_back"
              ? "Rolled back"
              : r.status === "rolling_back"
                ? "Waiting for CI to finish rolling back"
                : "Rollback failed"}
            {r.rollback_target_id ? (
              <>
                {" "}
                to{" "}
                <Link to={`/releases/${r.rollback_target_id}`} className="mono">
                  {r.rollback_target_version}
                </Link>
              </>
            ) : (
              " (no earlier healthy release found)"
            )}
          </span>
        </div>
        <dl className="facts">
          <dt>Reason</dt>
          <dd>{r.rollback_reason}</dd>
          <dt>Requested</dt>
          <dd>
            <Time iso={r.rollback_requested_at} />
          </dd>
          <dt>Completed</dt>
          <dd>
            <Time iso={r.rollback_completed_at} />
          </dd>
        </dl>
      </Card>
    );
  }

  if (!CAN_ROLL_BACK.has(r.status)) return null;

  return (
    <Card
      title="Rollback"
      actions={
        !open && (
          <button type="button" className="button danger" onClick={() => setOpen(true)}>
            Roll back
          </button>
        )
      }
    >
      {open ? (
        <form className="form inline-form" onSubmit={submit}>
          <label>
            Reason
            <input
              autoFocus
              required
              maxLength={500}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. error rate spike after deploy"
            />
          </label>
          <div className="form-actions">
            <button type="submit" className="button danger" disabled={submitting || !reason.trim()}>
              {submitting ? "Requesting…" : "Confirm rollback"}
            </button>
            <button type="button" className="button" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
          <ErrorNote error={error} />
        </form>
      ) : (
        <p className="muted small">
          Marks this release as rolling back to the previous healthy release in {r.environment} and
          notifies CI if the service has a rollback webhook.
        </p>
      )}
    </Card>
  );
}

const EVENT_TONE: Record<string, string> = {
  "deployment.failed": "bad",
  "verification.failed": "bad",
  "rollback.failed": "bad",
  "rollback.requested": "warn",
  "verification.passed": "good",
  "rollback.completed": "good",
};

function Timeline({ events }: { events: ReleaseEvent[] }) {
  if (events.length === 0) return <p className="empty">No events yet.</p>;
  return (
    <ol className="timeline" data-testid="timeline">
      {events.map((e) => (
        <li key={e.id} className={`tl tl-${EVENT_TONE[e.event_type] ?? "neutral"}`}>
          <span className="tl-type mono">{e.event_type}</span>
          <span className="tl-source">{e.source}</span>
          <Time iso={e.received_at} />
          {describePayload(e) && <span className="tl-detail">{describePayload(e)}</span>}
        </li>
      ))}
    </ol>
  );
}

function describePayload(e: ReleaseEvent): string | null {
  const p = e.payload;
  switch (e.event_type) {
    case "rollback.requested":
      return `${p.reason ?? ""}${p.target_version ? ` → ${p.target_version}` : ""}`;
    case "verification.failed":
      return `${p.failed_checks} failed checks${p.last_error ? ` (${p.last_error})` : ""}`;
    case "verification.started":
      return `${p.checks_required} checks against ${p.health_url}`;
    case "verification.skipped":
      return String(p.reason ?? "");
    default:
      return p.actor ? `by ${p.actor}` : null;
  }
}
