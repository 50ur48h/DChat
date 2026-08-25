/**
 * A value this browser remembers, read the way React wants it read.
 *
 * **Why this exists rather than `useState` plus an effect.** The obvious shape —
 * start at a default, then `setState` from an effect once `localStorage` is
 * reachable — is a cascading render, and `react-hooks/set-state-in-effect`
 * rejects it. `useSyncExternalStore` is the primitive for precisely this case: a
 * value that lives outside React, with a separate snapshot for the server, which
 * is what keeps server HTML and first client render identical and avoids a
 * hydration mismatch.
 *
 * It also buys something the effect version did not have: **every component
 * reading the same key stays in step**, because a write notifies all of them.
 * The theme toggle in Settings and any other reader of the theme cannot drift.
 *
 * **Storage can throw, not merely be empty.** A browser with site data blocked
 * raises on access rather than returning null, so both directions are wrapped.
 * A browser that refuses to remember gets the default and a value that still
 * works for the session.
 *
 * Values are strings on purpose. Everything stored here is a small enumeration —
 * a theme, a collapsed flag — and `getSnapshot` must return something that
 * compares equal between calls or React re-renders forever; primitives do,
 * parsed objects do not.
 *
 * **And because they are primitives, `get` reads storage every time rather than
 * caching.** A cached copy is the obvious optimisation and it is wrong here:
 * `Object.is` on a string already compares by value, so the cache buys nothing
 * React needs, and it introduces a way for the module to disagree with storage
 * that nothing invalidates — clearing site data does not fire a `storage` event
 * in the window that did it. The read is one synchronous lookup of a short
 * string; correctness is worth more than that.
 */

export interface Persisted<T extends string> {
  /** The stored value, or the default. Safe to call during render. */
  get: () => T;
  /** Server-render snapshot: always the default, since there is no storage. */
  getServer: () => T;
  set: (value: T) => void;
  subscribe: (listener: () => void) => () => void;
}

export function createPersisted<T extends string>(
  key: string,
  fallback: T,
  isValid: (value: string) => value is T,
): Persisted<T> {
  const listeners = new Set<() => void>();

  const read = (): T => {
    try {
      const raw = window.localStorage.getItem(key);
      return raw !== null && isValid(raw) ? raw : fallback;
    } catch {
      return fallback;
    }
  };

  return {
    get: read,
    getServer: () => fallback,
    set: (value: T) => {
      try {
        window.localStorage.setItem(key, value);
      } catch {
        // Applies for this session and will not survive a reload. Nobody can
        // act on that, so it is not surfaced.
      }
      for (const listener of listeners) listener();
    },
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      // Another tab changing the same key is a real update, and the `storage`
      // event is the only notification of it.
      const onStorage = (event: StorageEvent) => {
        if (event.key !== key) return;
        listener();
      };
      window.addEventListener("storage", onStorage);
      return () => {
        listeners.delete(listener);
        window.removeEventListener("storage", onStorage);
      };
    },
  };
}
