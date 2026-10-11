import { useCallback, useEffect, useRef, useState } from "react";

interface Resource<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
  reload: () => Promise<void>;
}

/**
 * Fetch data when `key` changes, optionally re-fetching every `pollMs` while
 * `shouldPoll(data)` is true. Stale responses from an earlier key are dropped.
 */
export function useResource<T>(
  fetcher: () => Promise<T>,
  key: string,
  options: { pollMs?: number; shouldPoll?: (data: T) => boolean } = {},
): Resource<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(true);
  const generation = useRef(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(async () => {
    const current = ++generation.current;
    try {
      const result = await fetcherRef.current();
      if (current !== generation.current) return;
      setData(result);
      setError(undefined);
    } catch (err) {
      if (current !== generation.current) return;
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    setData(undefined);
    void load();
  }, [key, load]);

  const { pollMs, shouldPoll } = options;
  const polling = pollMs !== undefined && data !== undefined && (shouldPoll?.(data) ?? true);
  useEffect(() => {
    if (!polling) return;
    const timer = window.setInterval(() => void load(), pollMs);
    return () => window.clearInterval(timer);
  }, [polling, pollMs, load]);

  return { data, error, loading, reload: load };
}
