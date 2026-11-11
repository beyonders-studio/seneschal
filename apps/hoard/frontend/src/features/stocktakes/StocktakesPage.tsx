import { ClipboardList } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StatusMessage } from "@/ui/status-message";

/** The stocktakes list, under Stock. */
export function StocktakesPage() {
  const { t } = useTranslation("movements");
  return (
    <StatusMessage
      icon={<ClipboardList />}
      title={t("stocktakes.emptyTitle")}
      description={t("stocktakes.emptyBody")}
    />
  );
}
