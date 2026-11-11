import { useTranslation } from "react-i18next";

/** One stocktake, under Stock. */
export function StocktakePage({ stocktakeId }: { stocktakeId: number }) {
  const { t } = useTranslation("movements");
  return <h2 className="text-xl font-semibold tracking-tight">{t("stocktakes.pageTitle")}</h2>;
}
