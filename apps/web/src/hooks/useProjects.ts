import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type ProjectEnvelope } from '../api';

/** Projects for the picker; refreshed on demand, ignoring responses that arrive out of order. */
export function useProjects() {
  const [projects, setProjects] = useState<ProjectEnvelope[]>([]);
  const latest = useRef(0);
  const refresh = useCallback(() => {
    const request = ++latest.current;
    return api
      .listProjects()
      .then((r) => request === latest.current && setProjects(r.projects))
      .catch(() => request === latest.current && setProjects([])); // the ID field still works
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  return { projects, refresh };
}
