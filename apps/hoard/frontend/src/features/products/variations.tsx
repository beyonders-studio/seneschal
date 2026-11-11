import { useTranslation } from "react-i18next";

import type { ProductStatus, ProductVariationRead } from "@/api/generated/hoard.schemas";
import { Badge } from "@/ui/badge";
import { listFormat } from "@/ui/lib/intl";

/** A variation's name: its attributes' values, or the words for the
 *  default variation when it has none. */
export function useVariationName(): (variation: ProductVariationRead) => string {
  const { t, i18n } = useTranslation("catalog");
  const list = listFormat(i18n.resolvedLanguage, { type: "unit" });
  return (variation) => {
    const values = Object.values(variation.attributes);
    return values.length ? list.format(values) : t("products.defaultVariation");
  };
}

/** Marks a product no longer sold; an active one carries no mark. */
export function StatusBadge({ status }: { status: ProductStatus }) {
  const { t } = useTranslation("catalog");
  if (status === "active") return null;
  return <Badge variant="outline">{t(`products.statuses.${status}`)}</Badge>;
}
