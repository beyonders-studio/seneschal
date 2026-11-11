import axios from "axios";
import type { TFunction } from "i18next";

/**
 * Why Hoard refused a call, in the reader's words: the API's code (its
 * `detail`) looked up as `errors.<code>` in `t`'s own namespace, then among
 * Hoard's common codes (`hoard:errors.<code>`), else the words `fallbackKey`
 * names. Each area maps its own codes in its namespace.
 */
export function errorMessage(error: unknown, t: TFunction, fallbackKey: string): string {
  const detail = axios.isAxiosError(error) ? error.response?.data?.detail : undefined;
  if (typeof detail !== "string") return t(fallbackKey);
  return t([`errors.${detail}`, `hoard:errors.${detail}`, fallbackKey]);
}
