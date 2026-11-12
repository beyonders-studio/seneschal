import { useTranslation } from "react-i18next";

import type { ProductDetail } from "@/api/generated/hoard.schemas";

/** A product's recipes on its page: each variation's, or a bundle's parts. */
export function RecipeSection({ product }: { product: ProductDetail }) {
  const { t } = useTranslation("recipes");
  return <p className="text-sm text-muted-foreground">{t("section.empty")}</p>;
}
