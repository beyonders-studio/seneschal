import axios from "axios";
import { Loader2 } from "lucide-react";
import { createContext, type ReactNode, useContext, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { exchangeHandoff, readMe } from "@/api/generated/auth/auth";
import type { HandoffExchange, Me } from "@/api/generated/hoard.schemas";
import { apiClient } from "@/api/mutator";
import { StandaloneBar } from "@/components/StandaloneBar";
import i18n from "@/i18n";
import { browser } from "@/lib/browser";
import {
  awaitHandoff,
  reportError,
  subscribeToClock,
  subscribeToLocale,
  subscribeToTheme,
} from "@/lib/parentBridge";
import {
  forgetSignInError,
  goToSignIn,
  renewSession,
  renewWhenEnded,
  signInError,
} from "@/lib/standalone";
import { applyHostTheme } from "@/lib/theme";
import { Button } from "@/ui/button";
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

type SessionState =
  | { status: "connecting" }
  | { status: "failed" }
  /** At Hoard's own address: the reader couldn't sign in, for `code`. */
  | { status: "notSignedIn"; code: string }
  | { status: "ready"; me: Me };

const SessionContext = createContext<Me | null>(null);

/** Who is signed in, where and with what: for anything under `SessionGate`. */
export function useSession(): Me {
  const me = useContext(SessionContext);
  if (!me) throw new Error("useSession needs a SessionGate above it");
  return me;
}

/**
 * Starts Hoard's session, then shows the page.
 *
 * Framed by Initiative, it waits for Initiative's handoff over the parent
 * bridge, takes the reader's language, appearance and clock from it,
 * exchanges its token for Hoard's own session, and reads who that session is
 * for, then follows the reader's language, appearance and clock as they
 * change them in Initiative.
 *
 * At Hoard's own address, it reads the session the reader's sign-in holds,
 * renewing it when it has ended, and sends them to sign in with Initiative
 * when there is none; the browser's language and the OS appearance stand.
 */
export function SessionGate({ children }: { children: ReactNode }) {
  const [framed] = useState(browser.framed);
  return framed ? (
    <FramedSession>{children}</FramedSession>
  ) : (
    <StandaloneSession>{children}</StandaloneSession>
  );
}

function FramedSession({ children }: { children: ReactNode }) {
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

  return <Session state={state}>{children}</Session>;
}

function StandaloneSession({ children }: { children: ReactNode }) {
  // Read once, before anything changes the address.
  const [code] = useState(signInError);
  const [state, setState] = useState<SessionState>(
    code === null ? { status: "connecting" } : { status: "notSignedIn", code }
  );

  useEffect(() => {
    const stop = renewWhenEnded(apiClient);
    let cancelled = false;
    if (code !== null) {
      forgetSignInError();
    } else {
      void (async () => {
        try {
          const me = await renewSession();
          if (cancelled) return;
          if (me) setState({ status: "ready", me });
          else goToSignIn();
        } catch {
          if (!cancelled) setState({ status: "failed" });
        }
      })();
    }
    return () => {
      cancelled = true;
      stop();
    };
  }, [code]);

  return (
    <Session state={state} standalone>
      {children}
    </Session>
  );
}

function Session({
  state,
  standalone = false,
  children,
}: {
  state: SessionState;
  standalone?: boolean;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  switch (state.status) {
    case "ready":
      return (
        <SessionContext value={state.me}>
          {standalone && <StandaloneBar />}
          {children}
        </SessionContext>
      );
    case "notSignedIn":
      return (
        <StatusMessage
          title={t("session.notSignedInTitle")}
          description={t([`errors.${state.code}`, "errors.SIGN_IN_FAILED"])}
          action={<Button onClick={() => goToSignIn("/")}>{t("session.signIn")}</Button>}
        />
      );
    case "failed":
      return (
        <StatusMessage title={t("session.failedTitle")} description={t("session.failedBody")} />
      );
    case "connecting":
      return (
        <StatusMessage
          icon={<Loader2 className="animate-spin" />}
          title={t(standalone ? "session.signingIn" : "session.connecting")}
        />
      );
  }
}
