import { createFileRoute } from "@tanstack/react-router";
import { Boxes } from "lucide-react";
import { useTranslation } from "react-i18next";

import { HoardPage } from "@/components/HoardPage";
import { StatusMessage } from "@/ui/status-message";

/** Stock, opened inside an initiative. */
export const Route = createFileRoute("/stock")({ component: StockPage });

function StockPage() {
  const { t } = useTranslation();
  return (
    <HoardPage title={t("stock.title")}>
      <StatusMessage
        icon={<Boxes />}
        title={t("stock.emptyTitle")}
        description={t("stock.emptyBody")}
      />
    </HoardPage>
  );
}
