import { ShoppingCart } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StatusMessage } from "@/ui/status-message";

/** The purchases list, under Stock. */
export function PurchasesPage() {
  const { t } = useTranslation("movements");
  return (
    <StatusMessage
      icon={<ShoppingCart />}
      title={t("purchases.emptyTitle")}
      description={t("purchases.emptyBody")}
    />
  );
}
