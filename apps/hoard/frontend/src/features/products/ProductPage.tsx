import { useTranslation } from "react-i18next";

/** One product, under Stock. */
export function ProductPage({ productId }: { productId: number }) {
  const { t } = useTranslation("catalog");
  return <h2 className="text-xl font-semibold tracking-tight">{t("products.pageTitle")}</h2>;
}
