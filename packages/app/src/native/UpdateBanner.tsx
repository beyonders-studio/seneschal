import { App } from "@capacitor/app";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/ui/button";
import { host } from "@/ui/host";

import type { AppIdentity } from "../session";
import { checkForUpdate, install, type Update } from "./update";

/**
 * The phone app's word that a newer bundle is ready, with a restart into it;
 * or that the bundle its server serves needs a newer phone app, and where to
 * get one. Asked when the app opens and each time it comes back to the front.
 */
export function UpdateBanner({ app, server }: { app: AppIdentity; server: string }) {
  const { t } = useTranslation("app");
  const [update, setUpdate] = useState<Update | null>(null);

  useEffect(() => {
    let checking = false;
    const check = async () => {
      if (checking) return;
      checking = true;
      try {
        setUpdate(await checkForUpdate(server, app.id, app.version));
      } catch {
        // A download that failed is tried again the next time.
      } finally {
        checking = false;
      }
    };
    void check();
    const listening = App.addListener("appStateChange", ({ isActive }) => {
      if (isActive) void check();
    });
    return () => void listening.then((handle) => handle.remove());
  }, [server, app.id, app.version]);

  if (!update) return null;
  const words = { app: app.name, version: update.version };
  return (
    <div
      role="status"
      className="fixed inset-x-0 bottom-0 z-50 flex flex-wrap items-center justify-between gap-2 border-t bg-background px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] shadow-md"
    >
      <span className="text-sm">
        {t(update.kind === "ready" ? "update.ready" : "update.nativeRequired", words)}
      </span>
      {update.kind === "ready" ? (
        <Button size="sm" onClick={() => void install(update.bundle)}>
          {t("update.restart")}
        </Button>
      ) : (
        <Button
          size="sm"
          variant="outline"
          onClick={() => host.openExternal(app.phone?.appsUrl ?? "")}
        >
          {t("update.getApp")}
        </Button>
      )}
    </div>
  );
}
