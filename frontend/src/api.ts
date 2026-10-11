export type ReleaseStatus =
  | "deploying"
  | "verifying"
  | "healthy"
  | "unhealthy"
  | "failed"
  | "rolling_back"
  | "rolled_back";

export const RELEASE_STATUSES: ReleaseStatus[] = [
  "deploying",
  "verifying",
  "healthy",
  "unhealthy",
  "failed",
  "rolling_back",
  "rolled_back",
];

/** Statuses that will change on their own; pages poll while a release is in one. */
export const IN_PROGRESS: ReadonlySet<ReleaseStatus> = new Set([
  "deploying",
  "verifying",
  "rolling_back",
]);

export const CAN_ROLL_BACK: ReadonlySet<ReleaseStatus> = new Set([
  "verifying",
  "healthy",
  "unhealthy",
  "failed",
]);

export interface Release {
  id: number;
  service: string;
  environment: string;
  version: string;
  commit_sha: string | null;
  status: ReleaseStatus;
  health_url: string | null;
  ci_run_url: string | null;
  triggered_by: string | null;
  created_at: string;
  deployed_at: string | null;
  verified_at: string | null;
  rollback_target_id: number | null;
  rollback_reason: string | null;
  rollback_requested_at: string | null;
  rollback_completed_at: string | null;
  health: { passed: number; failed: number; required: number };
  event_count: number;
}

export interface HealthCheck {
  id: number;
  sequence: number;
  outcome: "passed" | "failed";
  http_status: number | null;
  latency_ms: number | null;
  error: string | null;
  checked_at: string;
}

export interface ReleaseEvent {
  id: number;
  service_id: number;
  release_id: number | null;
  event_type: string;
  source: string;
  payload: Record<string, unknown>;
  received_at: string;
}

export interface ReleaseDetail extends Release {
  rollback_target_version: string | null;
  health_checks: HealthCheck[];
  events: ReleaseEvent[];
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Service {
  id: number;
  name: string;
  repository: string | null;
  default_health_url: string | null;
  auto_rollback: boolean;
  rollback_webhook_url: string | null;
  created_at: string;
}

export interface ServiceStats {
  service: string;
  releases: number;
  failed: number;
  change_failure_rate: number;
  last_release_status: ReleaseStatus | null;
  last_release_version: string | null;
  last_release_at: string | null;
}

export interface Summary {
  window_days: number;
  releases: number;
  by_status: Record<ReleaseStatus, number>;
  change_failure_rate: number;
  services: ServiceStats[];
}

export interface NewRelease {
  service: string;
  environment: string;
  version: string;
  commit_sha?: string;
  health_url?: string;
  triggered_by?: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function describeError(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${d.loc?.at(-1) ?? "body"}: ${d.msg}`)
      .join("; ");
  }
  return `Request failed (${status})`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const body = response.headers.get("content-type")?.includes("json")
    ? await response.json()
    : null;
  if (!response.ok) throw new ApiError(response.status, describeError(body, response.status));
  return body as T;
}

function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

export const api = {
  summary: (windowDays = 7) => request<Summary>(`/api/stats/summary${query({ window_days: windowDays })}`),
  services: () => request<Service[]>("/api/services"),
  releases: (filters: {
    service?: string;
    environment?: string;
    status?: string;
    cursor?: string | null;
    limit?: number;
  }) => request<Page<Release>>(`/api/releases${query(filters)}`),
  release: (id: number | string) => request<ReleaseDetail>(`/api/releases/${id}`),
  createRelease: (body: NewRelease) =>
    request<Release>("/api/releases", { method: "POST", body: JSON.stringify(body) }),
  rollback: (id: number, reason: string) =>
    request<Release>(`/api/releases/${id}/rollback`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
};
