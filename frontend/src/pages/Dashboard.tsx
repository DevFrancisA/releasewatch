import { useState } from "react";
import { Link } from "react-router-dom";
import { api, RELEASE_STATUSES } from "../api";
import { Card, ErrorNote, Loading, StatusBadge, statusLabel } from "../components/common";
import { ReleaseTable } from "../components/ReleaseTable";
import { useResource } from "../hooks";

const WINDOWS = [1, 7, 30, 90];

function percent(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

export function Dashboard() {
  const [windowDays, setWindowDays] = useState(7);
  const summary = useResource(() => api.summary(windowDays), `summary:${windowDays}`, {
    pollMs: 10_000,
  });
  const recent = useResource(() => api.releases({ limit: 10 }), "recent", { pollMs: 5_000 });

  const s = summary.data;
  const needsAttention = s ? s.by_status.unhealthy + s.by_status.rolling_back : 0;

  return (
    <>
      <div className="page-header">
        <h1>Dashboard</h1>
        <div className="segmented" role="group" aria-label="Time window">
          {WINDOWS.map((d) => (
            <button
              key={d}
              type="button"
              className={d === windowDays ? "active" : ""}
              onClick={() => setWindowDays(d)}
            >
              {d === 1 ? "24h" : `${d}d`}
            </button>
          ))}
        </div>
      </div>

      <ErrorNote error={summary.error} />
      {s ? (
        <>
          <div className="stats">
            <div className="stat">
              <span className="stat-label">Releases</span>
              <span className="stat-value" data-testid="stat-releases">
                {s.releases}
              </span>
            </div>
            <div className="stat">
              <span className="stat-label">Change failure rate</span>
              <span className="stat-value">{percent(s.change_failure_rate)}</span>
            </div>
            <div className="stat">
              <span className="stat-label">Verifying now</span>
              <span className="stat-value">{s.by_status.verifying + s.by_status.deploying}</span>
            </div>
            <div className={`stat ${needsAttention ? "stat-alert" : ""}`}>
              <span className="stat-label">Needs attention</span>
              <span className="stat-value">{needsAttention}</span>
            </div>
          </div>

          <StatusBreakdown byStatus={s.by_status} total={s.releases} />

          <div className="grid-2">
            <Card
              title="Recent releases"
              actions={
                <Link to="/releases" className="link">
                  View all →
                </Link>
              }
            >
              <ErrorNote error={recent.error} />
              {recent.data ? <ReleaseTable releases={recent.data.items} /> : <Loading />}
            </Card>

            <Card title="Services">
              {s.services.length === 0 ? (
                <p className="empty">
                  No services yet. Send a CI webhook or <Link to="/releases/new">register a release</Link>.
                </p>
              ) : (
                <div className="table-wrap">
                  <table className="table compact">
                    <thead>
                      <tr>
                        <th>Service</th>
                        <th className="num">Releases</th>
                        <th className="num">Failure rate</th>
                        <th>Latest</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.services.map((svc) => (
                        <tr key={svc.service}>
                          <td>
                            <Link to={`/releases?service=${encodeURIComponent(svc.service)}`}>
                              {svc.service}
                            </Link>
                          </td>
                          <td className="num">{svc.releases}</td>
                          <td className="num">{svc.releases ? percent(svc.change_failure_rate) : "—"}</td>
                          <td>
                            {svc.last_release_status ? (
                              <span className="latest">
                                <StatusBadge status={svc.last_release_status} />
                                <span className="mono">{svc.last_release_version}</span>
                              </span>
                            ) : (
                              <span className="muted">—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
        </>
      ) : (
        !summary.error && <Loading />
      )}
    </>
  );
}

function StatusBreakdown({
  byStatus,
  total,
}: {
  byStatus: Record<string, number>;
  total: number;
}) {
  if (!total) return null;
  const present = RELEASE_STATUSES.filter((st) => byStatus[st] > 0);
  return (
    <div className="breakdown">
      <div className="breakdown-bar" aria-hidden="true">
        {present.map((st) => (
          <span
            key={st}
            className={`seg seg-${st}`}
            style={{ flexGrow: byStatus[st] }}
            title={`${statusLabel(st)}: ${byStatus[st]}`}
          />
        ))}
      </div>
      <ul className="legend">
        {present.map((st) => (
          <li key={st}>
            <span className={`dot seg-${st}`} />
            {statusLabel(st)} <strong>{byStatus[st]}</strong>
          </li>
        ))}
      </ul>
    </div>
  );
}
