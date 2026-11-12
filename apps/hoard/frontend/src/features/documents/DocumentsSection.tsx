import { useTranslation } from "react-i18next";

/** The Initiative files linked to a lot, a product, a material or a base. */
export function DocumentsSection(_props: {
  of: "lot" | "product" | "material" | "base";
  id: number;
}) {
  const { t } = useTranslation("documents");
  return <p className="text-sm text-muted-foreground">{t("section.empty")}</p>;
}
