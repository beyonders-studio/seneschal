import axios from "axios";
import { Loader2 } from "lucide-react";
import { createContext, type ReactNode, useContext, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { exchangeHandoff, readMe } from "@/api/generated/auth/auth";
import type { HandoffExchange, Me } from "@/api/generated/hoard.schemas";
import i18n from "@/i18n";
import {
  awaitHandoff,
  reportError,
  subscribeToClock,
  subscribeToLocale,
  subscribeToTheme,
} from "@/lib/parentBridge";
import { applyHostTheme } from "@/lib/theme";
import { setClock } from "@/ui/host";
import { StatusMessage } from "@/ui/status-message";

/**
 * When Initiative and Hoard start together, the page can reach for the
 * backend before it listens. An exchange nobody answered is tried again on
 * these delays, inside the handoff's minute; an answer is final.
 */
const RETRY_DELAYS_MS = [500, 1_000, 2_000, 4_000, 8_000, 16_000];

const exchange = async (request: HandoffExchange): Promise<void> => {
  for (const delay of [...RETRY_DELAYS_MS, null]) {
    try {
      await exchangeHandoff(request);
      return;
    } catch (error) {
      if (delay === null || (axios.isAxiosError(error) && error.response)) throw error;
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
};

type SessionState = { status: "connecting" } | { status: "failed" } | { status: "ready"; me: Me };

const SessionContext = createContext<Me | null>(null);

/** Who is signed in, where and with what: for anything under `SessionGate`. */
export function useSession(): Me {
  const me = useContext(SessionContext);
  if (!me) throw new Error("useSession needs a SessionGate above it");
  return me;
}

/**
 * Starts Hoard's session, then shows the page. Waits for Initiative's handoff
 * over the parent bridge, takes the reader's language, appearance and clock
 * from it, exchanges its token for Hoard's own session, and reads who that
 * session is for. Follows the reader's language, appearance and clock as they
 * change them in Initiative.
 */
export function SessionGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [state, setState] = useState<SessionState>({ status: "connecting" });

  useEffect(() => {
    const stops = [
      subscribeToLocale((locale) => void i18n.changeLanguage(locale)),
      subscribeToTheme(applyHostTheme),
      subscribeToClock(setClock),
    ];
    let cancelled = false;
    void (async () => {
      try {
        const handoff = await awaitHandoff();
        if (cancelled) return;
        void i18n.changeLanguage(handoff.locale);
        if (handoff.theme) applyHostTheme(handoff.theme, handoff.theme_colors);
        setClock(handoff.clock);
        const { handoff_token, theme, locale } = handoff;
        await exchange({ handoff_token, theme, locale });
        const me = await readMe();
        if (!cancelled) setState({ status: "ready", me });
      } catch {
        if (cancelled) return;
        setState({ status: "failed" });
        reportError(i18n.t("hoard:session.failedBody"));
      }
    })();
    return () => {
      cancelled = true;
      for (const stop of stops) stop();
    };
  }, []);

  switch (state.status) {
    case "ready":
      return <SessionContext value={state.me}>{children}</SessionContext>;
    case "failed":
      return (
        <StatusMessage title={t("session.failedTitle")} description={t("session.failedBody")} />
      );
    case "connecting":
      return (
        <StatusMessage
          icon={<Loader2 className="animate-spin" />}
          title={t("session.connecting")}
        />
      );
  }
}
