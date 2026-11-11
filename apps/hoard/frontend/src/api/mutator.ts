import axios, { type AxiosRequestConfig } from "axios";

/**
 * The axios instance every API call goes through, and the mutator Orval's
 * generated client calls it by.
 *
 * Requests are relative: the generated paths carry `/api/v1`, and Hoard's
 * pages and API share an origin (the Vite server proxies them in dev), so the
 * session cookie the handoff exchange sets goes with each one.
 *
 * Reads no `import.meta.env`: Orval loads this file to generate the client.
 */
export const apiClient = axios.create();

export const apiMutator = <T>(config: AxiosRequestConfig, options?: AxiosRequestConfig) =>
  apiClient<T>({ ...config, ...options, headers: { ...config.headers, ...options?.headers } }).then(
    ({ data }) => data
  );

// The type names Orval's generated hooks expect beside a custom mutator.
export type ErrorType<Error> = Error;
export type BodyType<BodyData> = BodyData;
