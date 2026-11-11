import { ChartColumn } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StatusMessage } from "@/ui/status-message";

/** Reports on stock, inside an initiative. */
export function ReportsPage() {
  const { t } = useTranslation("reports");
  return (
    <StatusMessage icon={<ChartColumn />} title={t("emptyTitle")} description={t("emptyBody")} />
  );
}
