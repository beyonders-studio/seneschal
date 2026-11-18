import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/ui/button";
import { host } from "@/ui/host";

import { useAppIdentity, useOwnSession } from "./session";

/** A page the bar lists: where it is, and its name as people read it. */
export interface BarPage {
  to: string;
  label: string;
}

/**
 * What Initiative's frame gives an app's pages, outside it: the app's pages,
 * a way to open the app in another place, and signing out. A sign-in is for
 * one place, an initiative or the community, so switching signs in again, and
 * Initiative asks which.
 */
export function StandaloneBar({ pages }: { pages: BarPage[] }) {
  const { t } = useTranslation("app");
  const app = useAppIdentity();
  const own = useOwnSession();
  const [leaving, setLeaving] = useState(false);
  const { Link } = host;

  return (
    <header className="flex flex-wrap items-center gap-2 border-b px-4 py-2 pt-[max(0.5rem,env(safe-area-inset-top))] sm:px-6">
      <nav
        aria-label={t("session.pages", { app: app.name })}
        className="flex min-w-0 flex-1 gap-1 overflow-x-auto"
      >
        {pages.map(({ to, label }) => (
          <Button key={to} asChild variant="ghost" size="sm">
            <Link to={to}>{label}</Link>
          </Button>
        ))}
      </nav>
      {own && (
        <div className="flex gap-1">
          <Button variant="outline" size="sm" onClick={own.switchPlace}>
            {t("session.switchPlace")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={leaving}
            onClick={() => {
              setLeaving(true);
              void own.signOut().finally(() => setLeaving(false));
            }}
          >
            {t("session.signOut")}
          </Button>
        </div>
      )}
    </header>
  );
}
