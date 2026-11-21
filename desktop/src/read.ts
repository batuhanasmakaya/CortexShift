import { useEffect, useState } from 'react';
import { errorMessage } from './ipc';

export type Read<T> = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; value: T };

export function useRead<T>(load: () => Promise<T>, revision = 0): Read<T> {
  const [result, setResult] = useState<{ load: () => Promise<T>; read: Read<T>; revision: number }>();
  useEffect(() => {
    let current = true;
    void Promise.resolve().then(load).then(
      value => { if (current) setResult({ load, revision, read: { state: 'ready', value } }); },
      error => { if (current) setResult({ load, revision, read: { state: 'error', message: errorMessage(error) } }); },
    );
    return () => { current = false; };
  }, [load, revision]);
  return result?.load === load && result.revision === revision ? result.read : { state: 'loading' };
}
