import { useEffect, useState } from 'react';

/**
 * Read a localStorage value reactively. Updates when the key changes in another
 * tab (the `storage` event only fires cross-tab; same-tab writers re-render via
 * their own state). Returns null when the key is absent.
 */
export function useLocalStorage(key: string): string | null {
  const [value, setValue] = useState<string | null>(() => localStorage.getItem(key));

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === key) setValue(localStorage.getItem(key));
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [key]);

  return value;
}
