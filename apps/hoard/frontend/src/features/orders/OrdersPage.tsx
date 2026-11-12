import { ShoppingCart } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StatusMessage } from "@/ui/status-message";

/** The orders list, under Orders. */
export function OrdersPage() {
  const { t } = useTranslation("orders");
  return (
    <StatusMessage
      icon={<ShoppingCart />}
      title={t("list.emptyTitle")}
      description={t("list.emptyBody")}
    />
  );
}
