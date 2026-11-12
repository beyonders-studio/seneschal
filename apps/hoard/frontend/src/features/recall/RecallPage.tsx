import { Undo2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StatusMessage } from "@/ui/status-message";

/** A recall, under Stock: from a lot, or from an item and a date range,
 *  every lot made from it and every shipment it reached or may have. */
export function RecallPage() {
  const { t } = useTranslation("trace");
  return (
    <StatusMessage
      icon={<Undo2 />}
      title={t("recall.emptyTitle")}
      description={t("recall.emptyBody")}
    />
  );
}
