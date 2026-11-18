/**
 * The server a phone app talks to. A browser reaches the app at the origin it
 * loaded the pages from; the phone app carries its pages with it, so it is
 * told: the app's address on Initiative's cloud, or another the person types.
 */

import axios, { type AxiosInstance } from "axios";

import { AUTH, type SignInOffer } from "../auth";
import { getItem, KEYS, removeItem, setItem } from "./storage";

export type ServerRefusal = "notApp" | "unreachable";

export class ServerRefused extends Error {
  constructor(readonly reason: ServerRefusal) {
    super(reason);
  }
}

/** `address` as an origin: `https://` when it names none. */
export function originOf(address: string): string | null {
  const trimmed = address.trim();
  if (!trimmed) return null;
  try {
    const url = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`);
    return url.protocol === "https:" || url.protocol === "http:" ? url.origin : null;
  } catch {
    return null;
  }
}

/** The server chosen, or null before one is. */
export const chosenServer = (): string | null => getItem(KEYS.server);

/**
 * Choose `origin` once it answers as `app` and lets phones sign in. A new
 * server gets a new install id, as Initiative's app does.
 */
export async function chooseServer(origin: string, app: string): Promise<void> {
  let offer: SignInOffer;
  try {
    offer = (await axios.get<SignInOffer>(`${origin}${AUTH}/sign-in`, { timeout: 10_000 })).data;
  } catch (error) {
    throw new ServerRefused(axios.isAxiosError(error) && error.response ? "notApp" : "unreachable");
  }
  if (offer?.app !== app || !offer.device) throw new ServerRefused("notApp");
  if (getItem(KEYS.server) !== origin) {
    await removeItem(KEYS.renewal);
    await setItem(KEYS.install, crypto.randomUUID());
  }
  await setItem(KEYS.server, origin);
}

/** Forget the server, and the sign-in on it. */
export async function forgetServer(): Promise<void> {
  await removeItem(KEYS.renewal);
  await removeItem(KEYS.server);
}

/** Send `client`'s calls to `origin`. */
export function callServer(client: AxiosInstance, origin: string): void {
  client.defaults.baseURL = origin;
}

/** This install's id, made the first time it's asked for. */
export function installId(): string {
  const known = getItem(KEYS.install);
  if (known) return known;
  const made = crypto.randomUUID();
  void setItem(KEYS.install, made);
  return made;
}
