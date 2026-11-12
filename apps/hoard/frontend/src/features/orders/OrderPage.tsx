import { useTranslation } from "react-i18next";

/** One order, with its lines and shipments, under Orders. */
export function OrderPage({ orderId }: { orderId: number }) {
  const { t } = useTranslation("orders");
  return <h2 className="text-xl font-semibold tracking-tight">{t("order.pageTitle")}</h2>;
}
