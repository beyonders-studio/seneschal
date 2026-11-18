/**
 * An app's session, however the app was opened:
 *
 * - **framed** by Initiative (or its phone app): Initiative's handoff over the
 *   parent bridge, exchanged for the app's session; the reader's language,
 *   appearance and clock follow Initiative's;
 * - **at its own address** in a browser: the sign-in the reader holds there,
 *   renewed from its cookie, or Sign in with Initiative when there is none;
 * - **as its phone app**: the server it talks to, then a sign-in through the
 *   system browser, its session sent as a Bearer token.
 *
 * Outside Initiative's frame the app draws its own bar, with its pages, a way
 * to open it in another place, and signing out.
 */

import axios, { type AxiosInstance } from "axios";
import i18n from "i18next";
import { Loader2 } from "lucide-react";
import { createContext, type ReactNode, useContext, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/ui/button";
import { setClock } from "@/ui/host";
import { Input } from "@/ui/input";
import { StatusMessage } from "@/ui/status-message";

import { auth, bindApiClient, type Me, type Theme } from "./auth";
import { browser } from "./browser";
import { followBackButton, listenForLinks } from "./native/device";
import { isNative } from "./native/platform";
import {
  callServer,
  chooseServer,
  chosenServer,
  forgetServer,
  originOf,
  ServerRefused,
} from "./native/server";
import * as device from "./native/session";
import { beginSignIn, finishSignIn, isCallback, SignInFailure } from "./native/signIn";
import { UpdateBanner } from "./native/UpdateBanner";
import {
  awaitHandoff,
  reportError,
  subscribeToClock,
  subscribeToLocale,
  subscribeToTheme,
} from "./parentBridge";
import { type BarPage, StandaloneBar } from "./StandaloneBar";
import { HandheldScanner } from "./scan";
import {
  forgetSignInError,
  goToSignIn,
  renewSession,
  renewWhenEnded,
  signInError,
} from "./standalone";
import { applyHostTheme } from "./theme";

/** Who the app is, as its session needs to know. */
export interface AppIdentity {
  /** Its own name, as the core names it (`hoard`). */
  id: string;
  /** Its name as people read it (`Hoard`). */
  name: string;
  /** The version of the pages running: its VERSION, with a dev build's suffix. */
  version: string;
  /** The axios instance its calls go through. */
  client: AxiosInstance;
  /** Its phone app: the URL scheme the browser hands sign-ins back by, and
   *  its address on Initiative's cloud. */
  phone?: {
    scheme: string;
    cloudOrigin: string;
    /** Where a newer phone app is got, when a bundle needs one. */
    appsUrl: string;
  };
}

/**
 * When Initiative and the app start together, the page can reach for the
 * backend before it listens. An exchange nobody answered is tried again on
 * these delays, inside the handoff's minute; an answer is final.
 */
const RETRY_DELAYS_MS = [500, 1_000, 2_000, 4_000, 8_000, 16_000];

const exchange = async (request: {
  handoff_token: string;
  theme: Theme | null;
  locale: string;
}): Promise<void> => {
  for (const delay of [...RETRY_DELAYS_MS, null]) {
    try {
      await auth.exchangeHandoff(request);
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
  /** Outside Initiative's frame: the reader isn't signed in, for `code` when
   *  a sign-in failed. */
  | { status: "notSignedIn"; code: string | null }
  /** The phone app, before it knows its server. */
  | { status: "chooseServer" }
  | { status: "ready"; me: Me };

interface Signed {
  me: Me;
  /** Outside Initiative's frame: sign out, and open the app in another place. */
  own?: { signOut: () => Promise<void>; switchPlace: () => void };
}

const SessionContext = createContext<Signed | null>(null);
const IdentityContext = createContext<AppIdentity | null>(null);

/** Who is signed in, where and with what: for anything under `SessionGate`. */
export function useSession(): Me {
  const signed = useContext(SessionContext);
  if (!signed) throw new Error("useSession needs a SessionGate above it");
  return signed.me;
}

/** Signing out and switching place, outside Initiative's frame. */
export const useOwnSession = () => useContext(SessionContext)?.own;

export const useAppIdentity = (): AppIdentity => {
  const identity = useContext(IdentityContext);
  if (!identity) throw new Error("useAppIdentity needs a SessionGate above it");
  return identity;
};

/** Starts the app's session, then shows the page. */
export function SessionGate({
  app,
  pages,
  children,
}: {
  app: AppIdentity;
  /** The pages its bar lists outside Initiative's frame. */
  pages: { initiative: BarPage[]; community: BarPage[] };
  children: ReactNode;
}) {
  const [mode] = useState(() =>
    isNative() && app.phone ? "native" : browser.framed() ? "framed" : "standalone"
  );
  const [bound] = useState(() => {
    bindApiClient(app.client);
    return true;
  });
  const content = !bound ? null : mode === "native" ? (
    <NativeSession app={app}>{children}</NativeSession>
  ) : mode === "framed" ? (
    <FramedSession app={app}>{children}</FramedSession>
  ) : (
    <StandaloneSession app={app}>{children}</StandaloneSession>
  );
  return (
    <IdentityContext value={app}>
      <PagesContext value={pages}>{content}</PagesContext>
    </IdentityContext>
  );
}

const PagesContext = createContext<{ initiative: BarPage[]; community: BarPage[] }>({
  initiative: [],
  community: [],
});

function FramedSession({ app, children }: { app: AppIdentity; children: ReactNode }) {
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
        const me = await auth.readMe();
        if (!cancelled) setState({ status: "ready", me });
      } catch {
        if (cancelled) return;
        setState({ status: "failed" });
        reportError(i18n.t("app:session.failedBody", { app: app.name }));
      }
    })();
    return () => {
      cancelled = true;
      for (const stop of stops) stop();
    };
  }, [app.name]);

  return <Session state={state}>{children}</Session>;
}

function StandaloneSession({ app, children }: { app: AppIdentity; children: ReactNode }) {
  // Read once, before anything changes the address.
  const [code] = useState(signInError);
  const [state, setState] = useState<SessionState>(
    code === null ? { status: "connecting" } : { status: "notSignedIn", code }
  );

  useEffect(() => {
    const stop = renewWhenEnded(app.client);
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
  }, [app.client, code]);

  const own = {
    signOut: async () => {
      try {
        await auth.signOut();
      } finally {
        browser.go("/?sign_in_error=SIGNED_OUT");
      }
    },
    switchPlace: () => goToSignIn("/"),
  };
  return (
    <Session state={state} own={own} signIn={() => goToSignIn("/")}>
      {children}
    </Session>
  );
}

function NativeSession({ app, children }: { app: AppIdentity; children: ReactNode }) {
  const phone = app.phone as NonNullable<AppIdentity["phone"]>;
  const [server, setServer] = useState(chosenServer);
  const [state, setState] = useState<SessionState>(
    server === null ? { status: "chooseServer" } : { status: "connecting" }
  );

  // The session goes with every call, and Android's back button goes back.
  useEffect(() => {
    const stops = [device.sendSession(app.client), followBackButton()];
    return () => {
      for (const stop of stops) stop();
    };
  }, [app.client]);

  useEffect(() => {
    if (server === null) return;
    callServer(app.client, server);
    let cancelled = false;
    const settle = (next: SessionState) => {
      if (!cancelled) setState(next);
    };
    const stops = [
      device.onSignInEnded(() => settle({ status: "notSignedIn", code: "SIGN_IN_ENDED" })),
      listenForLinks((url) => {
        if (!isCallback(url, phone.scheme)) return;
        settle({ status: "connecting" });
        finishSignIn(url, server)
          .then(async (answer) => {
            if (answer) settle({ status: "ready", me: await device.adopt(answer) });
            else if (!device.holdsSignIn()) settle({ status: "notSignedIn", code: null });
          })
          .catch((error: unknown) =>
            settle({
              status: "notSignedIn",
              code: error instanceof SignInFailure ? error.code : "SIGN_IN_FAILED",
            })
          );
      }),
    ];
    if (device.holdsSignIn()) {
      device
        .renew()
        .then((me) => settle(me ? { status: "ready", me } : { status: "notSignedIn", code: null }))
        .catch(() => settle({ status: "failed" }));
    } else {
      settle({ status: "notSignedIn", code: null });
    }
    return () => {
      cancelled = true;
      for (const stop of stops) stop();
    };
  }, [app.client, server, phone.scheme]);

  if (state.status === "chooseServer" || server === null) {
    return (
      <ServerChoice
        app={app}
        onChosen={(origin) => {
          setState({ status: "connecting" });
          setServer(origin);
        }}
      />
    );
  }
  const own = {
    signOut: async () => {
      await device.signOut();
      setState({ status: "notSignedIn", code: "SIGNED_OUT" });
    },
    switchPlace: () => void beginSignIn(server),
  };
  return (
    <>
      <UpdateBanner app={app} server={server} />
      <Session
        state={state}
        own={own}
        signIn={() => void beginSignIn(server)}
        changeServer={async () => {
          await forgetServer();
          setServer(null);
          setState({ status: "chooseServer" });
        }}
      >
        {children}
      </Session>
    </>
  );
}

function ServerChoice({ app, onChosen }: { app: AppIdentity; onChosen: (origin: string) => void }) {
  const { t } = useTranslation("app");
  const phone = app.phone as NonNullable<AppIdentity["phone"]>;
  const [address, setAddress] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const connect = async (origin: string | null) => {
    if (!origin) {
      setProblem(t("server.notApp", { app: app.name }));
      return;
    }
    setChecking(true);
    setProblem(null);
    try {
      await chooseServer(origin, app.id);
      onChosen(origin);
    } catch (error) {
      const reason = error instanceof ServerRefused ? error.reason : "unreachable";
      setProblem(t(`server.${reason}`, { app: app.name }));
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="mx-auto flex min-h-full max-w-sm flex-col justify-center gap-4 p-6">
      <h1 className="text-xl font-semibold">{t("server.title", { app: app.name })}</h1>
      <p className="text-muted-foreground">{t("server.body", { app: app.name })}</p>
      <Button disabled={checking} onClick={() => void connect(phone.cloudOrigin)}>
        {t("server.cloud")}
      </Button>
      <form
        className="flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void connect(originOf(address));
        }}
      >
        <label className="text-sm font-medium" htmlFor="seneschal-server">
          {t("server.other")}
        </label>
        <Input
          id="seneschal-server"
          inputMode="url"
          autoCapitalize="off"
          autoCorrect="off"
          placeholder={t("server.addressPlaceholder")}
          value={address}
          onChange={(event) => setAddress(event.target.value)}
        />
        <Button type="submit" variant="outline" disabled={checking || !address.trim()}>
          {t("server.connect")}
        </Button>
      </form>
      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
    </div>
  );
}

function Session({
  state,
  own,
  signIn,
  changeServer,
  children,
}: {
  state: SessionState;
  own?: Signed["own"];
  signIn?: () => void;
  changeServer?: () => void;
  children: ReactNode;
}) {
  const { t } = useTranslation("app");
  const app = useAppIdentity();
  const pages = useContext(PagesContext);
  const words = { app: app.name };
  switch (state.status) {
    case "ready":
      return (
        <SessionContext value={{ me: state.me, own }}>
          <HandheldScanner />
          {own && (
            <StandaloneBar
              pages={state.me.initiative_id === null ? pages.community : pages.initiative}
            />
          )}
          {children}
        </SessionContext>
      );
    case "notSignedIn":
      return (
        <StatusMessage
          title={t("session.notSignedInTitle", words)}
          description={
            state.code === null
              ? undefined
              : t([`errors.${state.code}`, "errors.SIGN_IN_FAILED"], words)
          }
          action={
            <div className="flex flex-wrap justify-center gap-2">
              <Button onClick={signIn}>{t("session.signIn")}</Button>
              {changeServer && (
                <Button variant="outline" onClick={changeServer}>
                  {t("server.change")}
                </Button>
              )}
            </div>
          }
        />
      );
    case "failed":
      return (
        <StatusMessage
          title={t("session.failedTitle", words)}
          description={t("session.failedBody", words)}
        />
      );
    case "chooseServer":
    case "connecting":
      return (
        <StatusMessage
          icon={<Loader2 className="animate-spin" />}
          title={t(own ? "session.signingIn" : "session.connecting")}
        />
      );
  }
}
