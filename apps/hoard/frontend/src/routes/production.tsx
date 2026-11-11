import { createFileRoute } from "@tanstack/react-router";
import { Factory } from "lucide-react";
import { useTranslation } from "react-i18next";

import { HoardPage } from "@/components/HoardPage";
import { StatusMessage } from "@/ui/status-message";

/** Production, opened inside an initiative. */
export const Route = createFileRoute("/production")({ component: ProductionPage });

function ProductionPage() {
  const { t } = useTranslation();
  return (
    <HoardPage title={t("production.title")}>
      <StatusMessage
        icon={<Factory />}
        title={t("production.emptyTitle")}
        description={t("production.emptyBody")}
      />
    </HoardPage>
  );
}
