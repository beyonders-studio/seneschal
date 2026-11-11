/**
 * The messages Hoard's pages exchange with Initiative, the window that frames
 * them.
 *
 *   to Initiative
 *     {type: "initiative-plugin:ready"}             loaded: send the handoff
 *     {type: "initiative-plugin:error", message}    couldn't open; Initiative
 *                                                   shows the message instead
 *   from Initiative
 *     {type: "initiative-plugin:handoff", handoff_token, locale, theme?,
 *      theme_colors?, time_format?, week_start?, …}
 *     {type: "initiative-plugin:locale", locale}               language changed
 *     {type: "initiative-plugin:theme", theme, colors?}        appearance changed
 *     {type: "initiative-plugin:clock", time_format, week_start}  clock changed
 *
 * The ready ping carries nothing, so it goes to whichever window frames the
 * page. The handoff token is the credential: the backend checks Initiative's
 * signature on it, that it names Hoard, and that it is used once. Everything
 * else here is the reader's appearance, language and clock.
 */

import { type Clock, parseTimeFormat, parseWeekStart } from "@/ui/host";

/** The reader's resolved appearance; Initiative resolves "system" itself. */
export type HostTheme = "light" | "dark";

export interface Handoff {
  handoff_token: string;
  locale: string;
  /** Null when Initiative didn't say: the OS appearance stays. */
  theme: HostTheme | null;
  /** Initiative's colour tokens (custom property → value), or null. */
  theme_colors: Record<string, string> | null;
  /** The reader's clock; empty when Initiative didn't send one. */
  clock: Partial<Clock>;
}

const READY = "initiative-plugin:ready";
const ERROR = "initiative-plugin:error";
const HANDOFF = "initiative-plugin:handoff";
const LOCALE = "initiative-plugin:locale";
const THEME = "initiative-plugin:theme";
const CLOCK = "initiative-plugin:clock";

const isMessage = (data: unknown, type: string): data is Record<string, unknown> =>
  typeof data === "object" && data !== null && (data as { type?: unknown }).type === type;

const parseTheme = (value: unknown): HostTheme | null =>
  value === "light" || value === "dark" ? value : null;

/** A plain object's string-valued entries, or null when it has none. */
const parseColors = (value: unknown): Record<string, string> | null => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const colors = Object.fromEntries(
    Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === "string")
  );
  return Object.keys(colors).length > 0 ? colors : null;
};

/** The clock a message names: each half only when it is there. */
const parseClock = (data: Record<string, unknown>): Partial<Clock> => ({
  ...(data.time_format === undefined ? {} : { timeFormat: parseTimeFormat(data.time_format) }),
  ...(data.week_start === undefined ? {} : { weekStart: parseWeekStart(data.week_start) }),
});

/** A handoff message read into its parts, or null when it isn't one. */
export function parseHandoff(data: unknown): Handoff | null {
  if (!isMessage(data, HANDOFF)) return null;
  if (typeof data.handoff_token !== "string" || !data.handoff_token) return null;
  return {
    handoff_token: data.handoff_token,
    locale: typeof data.locale === "string" ? data.locale : "en",
    theme: parseTheme(data.theme),
    theme_colors: parseColors(data.theme_colors),
    clock: parseClock(data),
  };
}

/**
 * Say the page is ready and wait for Initiative's handoff. Rejects after
 * `timeoutMs`: Initiative answers a ready ping once, so there is nothing to
 * retry.
 */
export function awaitHandoff({ timeoutMs = 30_000 } = {}): Promise<Handoff> {
  return new Promise((resolve, reject) => {
    const stop = () => {
      window.removeEventListener("message", onMessage);
      clearTimeout(timer);
    };
    const onMessage = (event: MessageEvent) => {
      const handoff = parseHandoff(event.data);
      if (!handoff) return;
      stop();
      resolve(handoff);
    };
    const timer = setTimeout(() => {
      stop();
      reject(new Error("handoff timeout"));
    }, timeoutMs);
    window.addEventListener("message", onMessage);
    // Outside a frame the parent is this window, and the ping reaches nothing.
    window.parent.postMessage({ type: READY }, "*");
  });
}

/** Calls `receive` with each message of `type`; returns the unsubscribe. */
const subscribe = (type: string, receive: (data: Record<string, unknown>) => void) => {
  const listener = (event: MessageEvent) => {
    if (isMessage(event.data, type)) receive(event.data);
  };
  window.addEventListener("message", listener);
  return () => window.removeEventListener("message", listener);
};

/** Follow the reader's language as they change it in Initiative. */
export const subscribeToLocale = (onLocale: (locale: string) => void) =>
  subscribe(LOCALE, (data) => {
    if (typeof data.locale === "string") onLocale(data.locale);
  });

/** Follow the reader's appearance as they change it in Initiative. */
export const subscribeToTheme = (
  onTheme: (theme: HostTheme, colors: Record<string, string> | null) => void
) =>
  subscribe(THEME, (data) => {
    const theme = parseTheme(data.theme);
    if (theme) onTheme(theme, parseColors(data.colors));
  });

/** Follow the reader's clock as they change it in Initiative. */
export const subscribeToClock = (onClock: (clock: Partial<Clock>) => void) =>
  subscribe(CLOCK, (data) => onClock(parseClock(data)));

/** Tell Initiative the page couldn't open, so it shows `message` in its place. */
export function reportError(message: string): void {
  window.parent.postMessage({ type: ERROR, message }, "*");
}
