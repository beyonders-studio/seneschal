import { Package } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StatusMessage } from "@/ui/status-message";

/** The products list, under Stock. */
export function ProductsPage() {
  const { t } = useTranslation("catalog");
  return (
    <StatusMessage
      icon={<Package />}
      title={t("products.emptyTitle")}
      description={t("products.emptyBody")}
    />
  );
}
