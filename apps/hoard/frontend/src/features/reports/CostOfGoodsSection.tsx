import { useTranslation } from "react-i18next";

/** What the goods shipped cost, with the mass balance and fair-trade volume,
 *  in Reports. */
export function CostOfGoodsSection() {
  const { t } = useTranslation("reports");
  return <h2 className="text-lg font-semibold tracking-tight">{t("costOfGoods.title")}</h2>;
}
