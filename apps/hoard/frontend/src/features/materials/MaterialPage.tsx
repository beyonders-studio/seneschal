import { useTranslation } from "react-i18next";

/** One material, under Stock. */
export function MaterialPage({ materialId }: { materialId: number }) {
  const { t } = useTranslation("catalog");
  return <h2 className="text-xl font-semibold tracking-tight">{t("materials.pageTitle")}</h2>;
}
