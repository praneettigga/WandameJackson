import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { SCHEMA_VERSION } from '../constants';

export type ApiHealth = 'Checking API' | 'API connected' | 'API offline' | 'API version mismatch';

/** Connection state shown in the header, plus a guard to run before calls that need the API. */
export function useApiHealth() {
  const [health, setHealth] = useState<ApiHealth>('Checking API');
  const refresh = useCallback(async () => {
    const result = await api.health();
    if (result.status !== 'ok' || result.schemaVersion !== SCHEMA_VERSION) {
      setHealth('API version mismatch');
      throw new Error('The API version does not match this web app.');
    }
    setHealth('API connected');
  }, []);
  const ensureReady = useCallback(async () => {
    try {
      await refresh();
    } catch (cause) {
      setHealth((h) => (h === 'API version mismatch' ? h : 'API offline'));
      const detail = cause instanceof Error ? ` ${cause.message}` : '';
      throw new Error(
        `The API is unavailable. Make sure the local API is running, then retry.${detail}`,
      );
    }
  }, [refresh]);
  useEffect(() => {
    void refresh().catch(() =>
      setHealth((h) => (h === 'API version mismatch' ? h : 'API offline')),
    );
  }, [refresh]);
  return { health, ensureReady };
}
