import { Wheat } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StatusMessage } from "@/ui/status-message";

/** The materials list, under Stock. */
export function MaterialsPage() {
  const { t } = useTranslation("catalog");
  return (
    <StatusMessage
      icon={<Wheat />}
      title={t("materials.emptyTitle")}
      description={t("materials.emptyBody")}
    />
  );
}
