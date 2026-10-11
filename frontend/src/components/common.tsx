import type { ReactNode } from "react";
import type { Release, ReleaseStatus } from "../api";

const STATUS_LABELS: Record<ReleaseStatus, string> = {
  deploying: "Deploying",
  verifying: "Verifying",
  healthy: "Healthy",
  unhealthy: "Unhealthy",
  failed: "Failed",
  rolling_back: "Rolling back",
  rolled_back: "Rolled back",
};

export function statusLabel(status: ReleaseStatus): string {
  return STATUS_LABELS[status] ?? status;
}

export function StatusBadge({ status }: { status: ReleaseStatus }) {
  return (
    <span className={`badge badge-${status}`} data-testid="status-badge">
      {statusLabel(status)}
    </span>
  );
}

export function HealthMeter({ health }: { health: Release["health"] }) {
  if (health.required === 0) return <span className="muted">—</span>;
  const cells = Array.from({ length: Math.max(health.required, health.passed + health.failed) });
  return (
    <span
      className="meter"
      title={`${health.passed} passed, ${health.failed} failed of ${health.required}`}
      aria-label={`${health.passed} of ${health.required} health checks passed`}
    >
      {cells.map((_, i) => (
        <span
          key={i}
          className={
            i < health.passed ? "cell pass" : i < health.passed + health.failed ? "cell fail" : "cell"
          }
        />
      ))}
    </span>
  );
}

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
  ["second", 1],
];

export function timeAgo(iso: string, now = Date.now()): string {
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size || unit === "second") {
      return rtf.format(Math.round(seconds / size), unit);
    }
  }
  return "";
}

export function Time({ iso }: { iso: string | null }) {
  if (!iso) return <span className="muted">—</span>;
  return (
    <time dateTime={iso} title={new Date(iso).toLocaleString()}>
      {timeAgo(iso)}
    </time>
  );
}

export function shortSha(sha: string | null): string {
  return sha ? sha.slice(0, 7) : "—";
}

export function Card({
  title,
  actions,
  children,
}: {
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-header">
          {title && <h2>{title}</h2>}
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function ErrorNote({ error }: { error: Error | undefined }) {
  if (!error) return null;
  return (
    <p className="error" role="alert">
      {error.message}
    </p>
  );
}

export function Loading() {
  return <p className="muted">Loading…</p>;
}
