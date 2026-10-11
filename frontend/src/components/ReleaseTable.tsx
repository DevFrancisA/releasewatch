import { Link } from "react-router-dom";
import type { Release } from "../api";
import { HealthMeter, shortSha, StatusBadge, Time } from "./common";

export function ReleaseTable({ releases }: { releases: Release[] }) {
  if (releases.length === 0) {
    return <p className="empty">No releases match.</p>;
  }
  return (
    <div className="table-wrap">
      <table className="table" data-testid="release-table">
        <thead>
          <tr>
            <th>Release</th>
            <th>Environment</th>
            <th>Status</th>
            <th>Health checks</th>
            <th>Commit</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {releases.map((r) => (
            <tr key={r.id} data-testid={`release-row-${r.id}`}>
              <td>
                <Link to={`/releases/${r.id}`} className="release-link">
                  <strong>{r.service}</strong> <span className="mono">{r.version}</span>
                </Link>
              </td>
              <td>
                <span className="env">{r.environment}</span>
              </td>
              <td>
                <StatusBadge status={r.status} />
              </td>
              <td>
                <HealthMeter health={r.health} />
              </td>
              <td className="mono">{shortSha(r.commit_sha)}</td>
              <td>
                <Time iso={r.created_at} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
