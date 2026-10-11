import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, type NewRelease as NewReleaseBody } from "../api";
import { Card, ErrorNote } from "../components/common";

const EMPTY: Required<NewReleaseBody> = {
  service: "",
  environment: "production",
  version: "",
  commit_sha: "",
  health_url: "",
  triggered_by: "",
};

export function NewRelease() {
  const navigate = useNavigate();
  const [form, setForm] = useState(EMPTY);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<Error>();

  function field(name: keyof NewReleaseBody) {
    return {
      name,
      value: form[name],
      onChange: (e: { target: { value: string } }) => setForm({ ...form, [name]: e.target.value }),
    };
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(undefined);
    const body = Object.fromEntries(
      Object.entries(form)
        .map(([k, v]) => [k, v.trim()])
        .filter(([, v]) => v !== ""),
    ) as unknown as NewReleaseBody;
    try {
      const release = await api.createRelease(body);
      navigate(`/releases/${release.id}`);
    } catch (err) {
      setError(err as Error);
      setSubmitting(false);
    }
  }

  return (
    <>
      <div className="page-header">
        <h1>Register a release</h1>
      </div>
      <Card>
        <p className="muted small">
          Use this for deployments that did not come through a CI webhook. ReleaseWatch starts
          post-deployment health checks right away.
        </p>
        <form className="form" onSubmit={submit}>
          <div className="form-row">
            <label>
              Service
              <input
                {...field("service")}
                required
                pattern="[a-z0-9][a-z0-9._\-]{0,99}"
                title="Lowercase letters, digits, '.', '_' or '-'"
                placeholder="checkout"
              />
            </label>
            <label>
              Environment
              <input {...field("environment")} required pattern="[a-z0-9][a-z0-9._\-]{0,49}" />
            </label>
          </div>
          <div className="form-row">
            <label>
              Version
              <input {...field("version")} required maxLength={100} placeholder="v1.4.0" />
            </label>
            <label>
              Commit SHA <span className="optional">optional</span>
              <input {...field("commit_sha")} maxLength={64} placeholder="9f8e7d6" />
            </label>
          </div>
          <label>
            Health URL <span className="optional">optional</span>
            <input
              {...field("health_url")}
              type="url"
              placeholder="https://checkout.example.com/healthz"
            />
          </label>
          <label>
            Triggered by <span className="optional">optional</span>
            <input {...field("triggered_by")} maxLength={100} />
          </label>
          <ErrorNote error={error} />
          <div className="form-actions">
            <button type="submit" className="button primary" disabled={submitting}>
              {submitting ? "Registering…" : "Register release"}
            </button>
          </div>
        </form>
      </Card>
    </>
  );
}
