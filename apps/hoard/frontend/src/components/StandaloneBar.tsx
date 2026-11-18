import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { signOut } from "@/api/generated/auth/auth";
import { browser } from "@/lib/browser";
import { goToSignIn } from "@/lib/standalone";
import { useSession } from "@/session";
import { Button } from "@/ui/button";

/** Hoard's pages at initiative level, which Initiative's tabs show when it frames them. */
const INITIATIVE_PAGES = [
  { to: "/stock", word: "stock.title" },
  { to: "/production", word: "production.title" },
  { to: "/orders", word: "orders.title" },
  { to: "/reports", word: "reports.title" },
] as const;

/**
 * What Initiative's frame gives Hoard's pages, at Hoard's own address: its
 * pages, a way to open Hoard in another place, and signing out. A sign-in is
 * for one place, an initiative or the community, so switching signs in again,
 * and Initiative asks which.
 */
export function StandaloneBar() {
  const { t } = useTranslation();
  const { initiative_id } = useSession();
  const [leaving, setLeaving] = useState(false);

  const leave = async () => {
    setLeaving(true);
    try {
      await signOut();
    } finally {
      browser.go("/?sign_in_error=SIGNED_OUT");
    }
  };

  return (
    <header className="flex flex-wrap items-center gap-2 border-b px-4 py-2 sm:px-6">
      <nav aria-label={t("session.pages")} className="flex min-w-0 flex-1 gap-1 overflow-x-auto">
        {initiative_id === null ? (
          <Button asChild variant="ghost" size="sm">
            <Link to="/settings" activeProps={{ "aria-current": "page" }}>
              {t("settings.title")}
            </Link>
          </Button>
        ) : (
          INITIATIVE_PAGES.map(({ to, word }) => (
            <Button key={to} asChild variant="ghost" size="sm">
              <Link to={to} activeProps={{ "aria-current": "page" }}>
                {t(word)}
              </Link>
            </Button>
          ))
        )}
      </nav>
      <div className="flex gap-1">
        <Button variant="outline" size="sm" onClick={() => goToSignIn("/")}>
          {t("session.switchPlace")}
        </Button>
        <Button variant="outline" size="sm" disabled={leaving} onClick={() => void leave()}>
          {t("session.signOut")}
        </Button>
      </div>
    </header>
  );
}
