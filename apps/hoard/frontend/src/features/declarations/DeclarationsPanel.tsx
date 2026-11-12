import { useTranslation } from "react-i18next";

/** What a material, a product or a lot declares: its allergens and ingredients. */
export function DeclarationsPanel({ of, id }: { of: "material" | "product" | "lot"; id: number }) {
  const { t } = useTranslation("declarations");
  return <p className="text-sm text-muted-foreground">{t("panel.empty")}</p>;
}
