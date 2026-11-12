import { useTranslation } from "react-i18next";

/** One base, under Stock. */
export function BasePage({ baseId }: { baseId: number }) {
  const { t } = useTranslation("recipes");
  return <h2 className="text-xl font-semibold tracking-tight">{t("bases.pageTitle")}</h2>;
}
