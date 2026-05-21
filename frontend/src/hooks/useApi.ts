import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api/client';

interface UseApiState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

interface UseApiReturn<T> extends UseApiState<T> {
  refetch: () => void;
}

export function useApi<T>(
  path: string,
  params?: Record<string, string | number | undefined>,
  deps: unknown[] = []
): UseApiReturn<T> {
  const [state, setState] = useState<UseApiState<T>>({
    data: null,
    loading: true,
    error: null,
  });

  const paramsRef = useRef(params);
  paramsRef.current = params;

  const fetchData = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const data = await api.get<T>(path, paramsRef.current);
      setState({ data, loading: false, error: null });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unexpected error occurred';
      setState({ data: null, loading: false, error: message });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return { ...state, refetch: fetchData };
}

export function useMutation<TReq, TRes>(
  method: 'post' | 'put' | 'delete',
  path: string
) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutate = useCallback(
    async (body?: TReq): Promise<TRes | null> => {
      setLoading(true);
      setError(null);
      try {
        // delete takes no body; keep the call signatures unambiguous so the
        // union-typed dispatch type-checks under strict TS.
        const result =
          method === 'delete'
            ? await api.delete<TRes>(path)
            : await api[method]<TRes>(path, body);
        setLoading(false);
        return result;
      } catch (err) {
        const message = err instanceof Error ? err.message : 'An unexpected error occurred';
        setError(message);
        setLoading(false);
        return null;
      }
    },
    [method, path]
  );

  return { mutate, loading, error };
}
