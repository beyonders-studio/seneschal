import { createFileRoute } from "@tanstack/react-router";
import { Settings } from "lucide-react";
import { useTranslation } from "react-i18next";

import { HoardPage } from "@/components/HoardPage";
import { StatusMessage } from "@/ui/status-message";

/** Settings, opened at community level, which Initiative opens only to the
 *  community's admins. */
export const Route = createFileRoute("/settings")({ component: SettingsPage });

function SettingsPage() {
  const { t } = useTranslation();
  return (
    <HoardPage title={t("settings.title")}>
      <StatusMessage
        icon={<Settings />}
        title={t("settings.emptyTitle")}
        description={t("settings.emptyBody")}
      />
    </HoardPage>
  );
}
