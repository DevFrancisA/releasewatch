import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, RELEASE_STATUSES, type Release } from "../api";
import { Card, ErrorNote, Loading, statusLabel } from "../components/common";
import { ReleaseTable } from "../components/ReleaseTable";
import { useResource } from "../hooks";

const PAGE_SIZE = 25;
const FILTERS = ["service", "environment", "status"] as const;

export function Releases() {
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries(FILTERS.map((f) => [f, params.get(f) ?? ""])) as Record<
    (typeof FILTERS)[number],
    string
  >;
  const key = JSON.stringify(filters);

  const services = useResource(() => api.services(), "services");
  const firstPage = useResource(() => api.releases({ ...filters, limit: PAGE_SIZE }), key);

  // Extra pages appended by "Load more"; reset whenever the filters change.
  const [more, setMore] = useState<Release[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<Error>();

  useEffect(() => {
    setMore([]);
    setMoreError(undefined);
    setCursor(firstPage.data?.next_cursor ?? null);
  }, [firstPage.data]);

  async function loadMore() {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await api.releases({ ...filters, limit: PAGE_SIZE, cursor });
      setMore((prev) => [...prev, ...page.items]);
      setCursor(page.next_cursor);
    } catch (err) {
      setMoreError(err as Error);
    } finally {
      setLoadingMore(false);
    }
  }

  function setFilter(name: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { replace: true });
  }

  const releases = firstPage.data ? [...firstPage.data.items, ...more] : [];
  const environments = ["production", "staging", "canary"];

  return (
    <>
      <div className="page-header">
        <h1>Releases</h1>
        <Link to="/releases/new" className="button primary">
          Register release
        </Link>
      </div>

      <div className="filters">
        <label>
          Service
          <select value={filters.service} onChange={(e) => setFilter("service", e.target.value)}>
            <option value="">All services</option>
            {services.data?.map((s) => (
              <option key={s.id} value={s.name}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Environment
          <select
            value={filters.environment}
            onChange={(e) => setFilter("environment", e.target.value)}
          >
            <option value="">All environments</option>
            {[...new Set([...environments, filters.environment].filter(Boolean))].map((env) => (
              <option key={env} value={env}>
                {env}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select value={filters.status} onChange={(e) => setFilter("status", e.target.value)}>
            <option value="">Any status</option>
            {RELEASE_STATUSES.map((st) => (
              <option key={st} value={st}>
                {statusLabel(st)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <Card>
        <ErrorNote error={firstPage.error} />
        {firstPage.loading && !firstPage.data ? <Loading /> : <ReleaseTable releases={releases} />}
        <ErrorNote error={moreError} />
        {cursor && (
          <div className="load-more">
            <button type="button" className="button" onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? "Loading…" : "Load more"}
            </button>
          </div>
        )}
      </Card>
    </>
  );
}
