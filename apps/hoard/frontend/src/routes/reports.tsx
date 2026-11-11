import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { HoardPage } from "@/components/HoardPage";
import { ReportsPage } from "@/features/reports/ReportsPage";

/** Reports, opened inside an initiative. */
export const Route = createFileRoute("/reports")({ component: Reports });

function Reports() {
  const { t } = useTranslation();
  return (
    <HoardPage title={t("reports.title")}>
      <ReportsPage />
    </HoardPage>
  );
}
