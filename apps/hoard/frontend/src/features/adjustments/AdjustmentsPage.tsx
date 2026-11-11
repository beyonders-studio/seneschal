import { ArrowUpDown } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StatusMessage } from "@/ui/status-message";

/** Every change to stock, under Stock. */
export function AdjustmentsPage() {
  const { t } = useTranslation("movements");
  return (
    <StatusMessage
      icon={<ArrowUpDown />}
      title={t("adjustments.emptyTitle")}
      description={t("adjustments.emptyBody")}
    />
  );
}
