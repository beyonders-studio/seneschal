/**
 * What the phone app keeps on the phone, from Initiative's own app
 * (`frontend/src/lib/storage.ts`).
 *
 * The keys that let the phone act as the person (the renewal, a sign-in under
 * way) are kept in their own Preferences group, which the app's Android backup
 * rules leave out (`seneschal-credentials` in `packages/native`): a phone
 * restored from a backup keeps its server and signs in again. Everything is
 * read into memory once at boot, so reads are synchronous.
 */

import { Preferences } from "@capacitor/preferences";

export const KEYS = {
  /** The server the app talks to: its origin. */
  server: "seneschal-server",
  /** This install's id, which a device sign-in is bound to. */
  install: "seneschal-install",
  /** The renewal of the current sign-in. */
  renewal: "seneschal-renewal",
  /** A sign-in begun in the system browser, until it comes back. */
  pending: "seneschal-pending-sign-in",
} as const;

const DEFAULT_GROUP = "CapacitorStorage";
/** Named in the backup rules `packages/native` writes. */
export const CREDENTIALS_GROUP = "SeneschalCredentials";
const CREDENTIALS = new Set<string>([KEYS.renewal, KEYS.pending]);
const groupOf = (key: string) => (CREDENTIALS.has(key) ? CREDENTIALS_GROUP : DEFAULT_GROUP);

// The group is set for every call that follows, so calls run one at a time.
let queue: Promise<unknown> = Promise.resolve();
const inGroup = <T>(group: string, operation: () => Promise<T>): Promise<T> => {
  const run = queue.then(async () => {
    await Preferences.configure({ group });
    return operation();
  });
  queue = run.catch(() => undefined);
  return run;
};

const cache = new Map<string, string>();

/** Read what the phone keeps into memory. Call once, before the app renders. */
export async function loadStorage(): Promise<void> {
  cache.clear();
  for (const group of [DEFAULT_GROUP, CREDENTIALS_GROUP]) {
    const entries = await inGroup(group, async () => {
      const { keys } = await Preferences.keys();
      return Promise.all(
        keys.map(async (key) => [key, (await Preferences.get({ key })).value] as const)
      );
    });
    for (const [key, value] of entries) if (value !== null) cache.set(key, value);
  }
}

export const getItem = (key: string): string | null => cache.get(key) ?? null;

/** Seen at once; written for good when the promise settles. */
export function setItem(key: string, value: string): Promise<void> {
  cache.set(key, value);
  return inGroup(groupOf(key), () => Preferences.set({ key, value }));
}

export function removeItem(key: string): Promise<void> {
  cache.delete(key);
  return inGroup(groupOf(key), () => Preferences.remove({ key }));
}
