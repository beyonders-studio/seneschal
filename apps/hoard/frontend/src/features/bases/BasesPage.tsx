import { CookingPot } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StatusMessage } from "@/ui/status-message";

/** The bases list, under Stock. */
export function BasesPage() {
  const { t } = useTranslation("recipes");
  return (
    <StatusMessage
      icon={<CookingPot />}
      title={t("bases.emptyTitle")}
      description={t("bases.emptyBody")}
    />
  );
}
