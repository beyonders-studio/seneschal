/**
 * A phone app's sign-in, finished in the system browser (RFC 8252), as
 * Initiative's own app signs in (`frontend/src/lib/nativeSignIn.ts`).
 *
 * The app makes a PKCE pair and a state, keeps them, and opens the system
 * browser at the server's sign-in with `client=device`. The person signs in
 * with Initiative there; the server sends the browser back to
 * `<scheme>://sign-in/callback` with a code that only this verifier redeems,
 * and only from this install. A callback for a sign-in this app didn't begin,
 * or began too long ago, is ignored.
 */

import { Browser } from "@capacitor/browser";

import { AUTH, auth, type DeviceSession } from "../auth";
import { installId } from "./server";
import { getItem, KEYS, removeItem, setItem } from "./storage";

/** As long as the server keeps a sign-in pending. */
const PENDING_MS = 10 * 60 * 1000;
const CALLBACK = "sign-in/callback";

interface Pending {
  verifier: string;
  state: string;
  origin: string;
  startedAt: number;
}

/** Why a sign-in came back without a session: one of the server's codes. */
export class SignInFailure extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}

const base64url = (bytes: Uint8Array): string =>
  btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");

const random = (bytes: number) => base64url(crypto.getRandomValues(new Uint8Array(bytes)));

/** A PKCE verifier and its S256 challenge (RFC 7636). */
export async function pkcePair(): Promise<{ verifier: string; challenge: string }> {
  const verifier = random(32);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return { verifier, challenge: base64url(new Uint8Array(digest)) };
}

/**
 * Open the system browser at `origin`'s sign-in. What it needs to finish is
 * kept first: the phone often stops the app while the browser is in front,
 * and the callback then starts it afresh.
 */
export async function beginSignIn(origin: string): Promise<void> {
  const { verifier, challenge } = await pkcePair();
  const state = random(24);
  const pending: Pending = { verifier, state, origin, startedAt: Date.now() };
  await setItem(KEYS.pending, JSON.stringify(pending));
  const query = new URLSearchParams({
    client: "device",
    challenge,
    state,
    install: installId(),
  });
  await Browser.open({ url: `${origin}${AUTH}/sign-in/start?${query}` });
}

/**
 * The sign-in this app began against `origin` whose state `state` is, taken so
 * it is answered once. A callback with another state takes nothing, so a link
 * the app didn't ask for can't cancel the sign-in under way.
 */
function takePending(origin: string, state: string | null): Pending | null {
  const raw = getItem(KEYS.pending);
  if (!raw || !state) return null;
  let pending: Pending;
  try {
    pending = JSON.parse(raw) as Pending;
  } catch {
    void removeItem(KEYS.pending);
    return null;
  }
  if (pending.state !== state) return null;
  void removeItem(KEYS.pending);
  const fresh = Date.now() - pending.startedAt < PENDING_MS;
  return fresh && pending.origin === origin ? pending : null;
}

/** Whether `url` is the browser bringing a sign-in back to the app `scheme` names. */
export const isCallback = (url: string, scheme: string): boolean =>
  url.startsWith(`${scheme}://${CALLBACK}`);

/**
 * Finish the sign-in the callback `url` brings back: check it is the one this
 * app began, then spend its code for a session. Null when it is not this
 * app's to answer; throws {@link SignInFailure} when the sign-in failed.
 */
export async function finishSignIn(url: string, origin: string): Promise<DeviceSession | null> {
  const params = new URL(url).searchParams;
  const pending = takePending(origin, params.get("state"));
  if (!pending) return null;
  void Browser.close().catch(() => undefined);
  const error = params.get("error");
  if (error) throw new SignInFailure(error);
  const code = params.get("code");
  if (!code) throw new SignInFailure("SIGN_IN_FAILED");
  try {
    return await auth.deviceToken({ code, verifier: pending.verifier, install: installId() });
  } catch {
    throw new SignInFailure("SIGN_IN_FAILED");
  }
}
