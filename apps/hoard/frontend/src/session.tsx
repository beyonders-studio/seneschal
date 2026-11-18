import { type AppIdentity, SessionGate as SharedSessionGate } from "@seneschal/app/session";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { apiClient } from "@/api/mutator";

export { useSession } from "@seneschal/app/session";

/** Hoard, as its session knows it: in Initiative's frame, at its own address,
 *  and as its phone app. */
export const HOARD: AppIdentity = {
  id: "hoard",
  name: "Hoard",
  version: import.meta.env.VITE_APP_VERSION,
  client: apiClient,
  phone: {
    scheme: "hoard",
    cloudOrigin: "https://hoard.initiativetasks.com",
    appsUrl: "https://github.com/beyonders-studio/seneschal/releases",
  },
};

/** Starts Hoard's session, then shows the page (`@seneschal/app/session`). */
export function SessionGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const pages = {
    initiative: [
      { to: "/stock", label: t("stock.title") },
      { to: "/production", label: t("production.title") },
      { to: "/orders", label: t("orders.title") },
      { to: "/reports", label: t("reports.title") },
    ],
    community: [{ to: "/settings", label: t("settings.title") }],
  };
  return (
    <SharedSessionGate app={HOARD} pages={pages}>
      {children}
    </SharedSessionGate>
  );
}
