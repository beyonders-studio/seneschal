/**
 * A phone app's session. The session itself is kept in memory and sent as a
 * Bearer token; the renewal is kept on the phone, out of its backups, and
 * spent once for each new session, from this install. Renewing happens one at
 * a time: a renewal spent twice ends the sign-in.
 */

import { type AxiosInstance, type InternalAxiosRequestConfig, isAxiosError } from "axios";

import { AUTH, auth, type DeviceSession, type Me } from "../auth";
import { installId } from "./server";
import { getItem, KEYS, removeItem, setItem } from "./storage";

/** Renew this long before the session expires, rather than meet a 401. */
const EARLY_MS = 30_000;

let session: { token: string; expiresAt: number } | null = null;
let renewing: Promise<Me | null> | null = null;
const endings = new Set<() => void>();

/** Keep the session `answer` carries, and the renewal that replaces the one spent. */
export async function adopt(answer: DeviceSession): Promise<Me> {
  session = { token: answer.session, expiresAt: Date.parse(answer.session_expires_at) };
  await setItem(KEYS.renewal, answer.renewal);
  return answer.me;
}

/** Whether the phone holds a sign-in to renew. */
export const holdsSignIn = (): boolean => getItem(KEYS.renewal) !== null;

/** Call `ended` when the sign-in ends while the app runs. Returns the undo. */
export function onSignInEnded(ended: () => void): () => void {
  endings.add(ended);
  return () => endings.delete(ended);
}

async function forget(): Promise<void> {
  session = null;
  await removeItem(KEYS.renewal);
}

/**
 * Spend the renewal for a new session: who is signed in, or null when the
 * sign-in has ended. Throws when the server couldn't answer, which leaves the
 * renewal as it was.
 */
export function renew(): Promise<Me | null> {
  renewing ??= (async () => {
    const renewal = getItem(KEYS.renewal);
    if (!renewal) return null;
    try {
      return await adopt(await auth.deviceRefresh({ renewal, install: installId() }));
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 401) {
        await forget();
        return null;
      }
      throw error;
    }
  })().finally(() => {
    renewing = null;
  });
  return renewing;
}

/** End the sign-in here and on the server. The person stays signed in to Initiative. */
export async function signOut(): Promise<void> {
  const renewal = getItem(KEYS.renewal);
  await forget();
  if (renewal) await auth.deviceSignOut({ renewal, install: installId() }).catch(() => undefined);
}

type Retried = InternalAxiosRequestConfig & { _renewed?: boolean };

const ended = () => {
  for (const listener of endings) listener();
};

/**
 * Send the session with `client`'s calls, renewing it shortly before it
 * expires and once when a call finds it ended. Returns the undo.
 */
export function sendSession(client: AxiosInstance): () => void {
  const request = client.interceptors.request.use(async (config) => {
    if (config.url?.startsWith(`${AUTH}/device/`)) return config;
    if (session && Date.now() > session.expiresAt - EARLY_MS) {
      await renew().catch(() => undefined);
    }
    if (session) config.headers.set("Authorization", `Bearer ${session.token}`);
    return config;
  });
  const response = client.interceptors.response.use(undefined, async (error: unknown) => {
    const config = isAxiosError(error) ? (error.config as Retried | undefined) : undefined;
    const unauthorized = isAxiosError(error) && error.response?.status === 401;
    if (!unauthorized || !config || config._renewed || config.url?.startsWith(`${AUTH}/device/`)) {
      throw error;
    }
    if ((await renew()) === null) {
      ended();
      throw error;
    }
    return client({ ...config, _renewed: true } as Retried);
  });
  return () => {
    client.interceptors.request.eject(request);
    client.interceptors.response.eject(response);
  };
}
