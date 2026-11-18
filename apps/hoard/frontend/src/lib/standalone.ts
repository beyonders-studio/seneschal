/**
 * Hoard at its own address: nobody frames it, so the reader signs in with
 * their Initiative account (Sign in with Initiative), and their session is
 * renewed from the renewal the backend keeps in a cookie of its own.
 *
 * A renewal is spent once, and one presented again ends the whole sign-in.
 * So renewing happens one at a time across every tab the reader has open
 * (the Web Locks API), and a tab that waited first looks whether another tab
 * already renewed: their cookies are the same.
 */

import { type AxiosInstance, type InternalAxiosRequestConfig, isAxiosError } from "axios";

import { readMe, refreshSession } from "@/api/generated/auth/auth";
import type { Me } from "@/api/generated/hoard.schemas";
import { browser } from "@/lib/browser";

const SIGN_IN = "/api/v1/auth/sign-in/start";
const AUTH = "/api/v1/auth/";
const LOCK = "hoard:session:renew";
const SIGN_IN_ERROR = "sign_in_error";

const unauthorized = (error: unknown) => isAxiosError(error) && error.response?.status === 401;

/** Runs `work` while no other tab of Hoard's renews. */
const oneAtATime = <T>(work: () => Promise<T>): Promise<T> =>
  typeof navigator !== "undefined" && navigator.locks
    ? navigator.locks.request(LOCK, work)
    : work();

let renewing: Promise<Me | null> | null = null;

/**
 * Who is signed in, renewing the session when it has ended. Null when the
 * reader must sign in again; throws when Hoard or Initiative couldn't answer,
 * which leaves the sign-in as it was.
 */
export function renewSession(): Promise<Me | null> {
  renewing ??= oneAtATime(async () => {
    try {
      return await readMe();
    } catch (error) {
      if (!unauthorized(error)) throw error;
    }
    try {
      return await refreshSession();
    } catch (error) {
      if (unauthorized(error)) return null;
      throw error;
    }
  }).finally(() => {
    renewing = null;
  });
  return renewing;
}

/** Where the reader signs in, to come back to `next` (this page, by default). */
export const signInAddress = (next = `${window.location.pathname}${window.location.search}`) =>
  `${SIGN_IN}?${new URLSearchParams({ next })}`;

/** Send the reader to Initiative to sign in, and back here after. */
export const goToSignIn = (next?: string) => browser.go(signInAddress(next));

/**
 * Why the reader just failed to sign in, which the backend puts in the
 * address it sends them back to; null when they didn't.
 */
export const signInError = (): string | null =>
  new URL(window.location.href).searchParams.get(SIGN_IN_ERROR);

/** Take why out of the address, so a reload doesn't say it again. */
export function forgetSignInError(): void {
  const address = new URL(window.location.href);
  if (!address.searchParams.has(SIGN_IN_ERROR)) return;
  address.searchParams.delete(SIGN_IN_ERROR);
  window.history.replaceState(window.history.state, "", address);
}

type Retried = InternalAxiosRequestConfig & { _renewed?: boolean };

/**
 * Renew once when a call finds the session ended, then make the call again;
 * send the reader to sign in when it can't be renewed. Returns the undo.
 */
export function renewWhenEnded(client: AxiosInstance): () => void {
  const id = client.interceptors.response.use(undefined, async (error: unknown) => {
    const config = isAxiosError(error) ? (error.config as Retried | undefined) : undefined;
    if (!unauthorized(error) || !config || config._renewed || config.url?.startsWith(AUTH)) {
      throw error;
    }
    if ((await renewSession()) === null) {
      goToSignIn();
      throw error;
    }
    return client({ ...config, _renewed: true } as Retried);
  });
  return () => client.interceptors.response.eject(id);
}
