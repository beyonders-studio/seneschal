/**
 * The core's auth routes (`seneschal_core.auth` and `seneschal_core.sign_in`),
 * which every app serves under `/api/v1/auth`, called through the app's own
 * axios instance so its base address, interceptors and tests apply.
 */

import type { AxiosInstance } from "axios";

export type Theme = "light" | "dark";

/** Who is signed in, where, and where the organization stands. */
export interface Me {
  member_ref: string;
  community_ref: string;
  initiative_id: number | null;
  page: string;
  admin: boolean;
  permissions: string[];
  theme: Theme | null;
  locale: string | null;
  read_only: boolean;
  plan: string | null;
}

/** A phone app's session, its expiry and the renewal that renews it. */
export interface DeviceSession {
  session: string;
  session_expires_at: string;
  renewal: string;
  me: Me;
}

/** The sign-ins an app offers at its own address. */
export interface SignInOffer {
  app: string;
  browser: boolean;
  device: boolean;
}

export const AUTH = "/api/v1/auth";

let client: AxiosInstance | null = null;

/** Make `api` the instance every call here goes through. Called at boot. */
export function bindApiClient(api: AxiosInstance): void {
  client = api;
}

const api = (): AxiosInstance => {
  if (!client) throw new Error("call bindApiClient before the auth routes");
  return client;
};

const data = <T>(request: Promise<{ data: T }>): Promise<T> => request.then(({ data }) => data);

export const auth = {
  exchangeHandoff: (body: { handoff_token: string; theme: Theme | null; locale: string }) =>
    data(api().post<Me>(`${AUTH}/handoff/exchange`, body)),
  readMe: () => data(api().get<Me>(`${AUTH}/me`)),
  refresh: () => data(api().post<Me>(`${AUTH}/refresh`)),
  signOut: () => data(api().post<void>(`${AUTH}/sign-out`)),
  offer: () => data(api().get<SignInOffer>(`${AUTH}/sign-in`)),
  deviceToken: (body: { code: string; verifier: string; install: string }) =>
    data(api().post<DeviceSession>(`${AUTH}/device/token`, body)),
  deviceRefresh: (body: { renewal: string; install: string }) =>
    data(api().post<DeviceSession>(`${AUTH}/device/refresh`, body)),
  deviceSignOut: (body: { renewal: string; install: string }) =>
    data(api().post<void>(`${AUTH}/device/sign-out`, body)),
};
