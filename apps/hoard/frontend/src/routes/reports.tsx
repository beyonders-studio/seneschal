import { createFileRoute } from "@tanstack/react-router";
import { ChartColumn } from "lucide-react";
import { useTranslation } from "react-i18next";

import { HoardPage } from "@/components/HoardPage";
import { StatusMessage } from "@/ui/status-message";

/** Reports, opened inside an initiative. */
export const Route = createFileRoute("/reports")({ component: ReportsPage });

function ReportsPage() {
  const { t } = useTranslation();
  return (
    <HoardPage title={t("reports.title")}>
      <StatusMessage
        icon={<ChartColumn />}
        title={t("reports.emptyTitle")}
        description={t("reports.emptyBody")}
      />
    </HoardPage>
  );
}
