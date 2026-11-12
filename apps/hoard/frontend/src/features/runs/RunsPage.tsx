import { Factory } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StatusMessage } from "@/ui/status-message";

/** The production runs list, under Production. */
export function RunsPage() {
  const { t } = useTranslation("production");
  return (
    <StatusMessage
      icon={<Factory />}
      title={t("runs.emptyTitle")}
      description={t("runs.emptyBody")}
    />
  );
}
