import { useTranslation } from "react-i18next";

/** One purchase, under Stock. */
export function PurchasePage({ purchaseId }: { purchaseId: number }) {
  const { t } = useTranslation("movements");
  return <h2 className="text-xl font-semibold tracking-tight">{t("purchases.pageTitle")}</h2>;
}
