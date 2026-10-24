import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { API_URLS, defaultBackend, getBackend, setBackend, type Backend } from '../api/client';

/** Backend selection state, persisted in localStorage 'fincore-backend'. */
export function useBackend() {
  const queryClient = useQueryClient();
  const [backend, setBackendState] = useState<Backend>(() => getBackend() ?? defaultBackend);

  const switchBackend = useCallback(
    (next: Backend) => {
      setBackend(next);
      setBackendState(next);
      void queryClient.invalidateQueries();
    },
    [queryClient],
  );

  return { backend, switchBackend, apiUrl: API_URLS[backend] };
}
