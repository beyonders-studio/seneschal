import { useTranslation } from "react-i18next";

import type { ProductVariationRead } from "@/api/generated/hoard.schemas";
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
