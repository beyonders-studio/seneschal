import { Store } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StatusMessage } from "@/ui/status-message";

/** The stores' listings, each with the item it sells, under Orders. */
export function ListingsPage() {
  const { t } = useTranslation("stores");
  return (
    <StatusMessage
      icon={<Store />}
      title={t("listings.emptyTitle")}
      description={t("listings.emptyBody")}
    />
  );
}
