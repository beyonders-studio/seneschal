import { useTranslation } from "react-i18next";

import type { ProductDetail } from "@/api/generated/hoard.schemas";

/** A product in each connected store, on its page: its listings, and the
 *  store's own title, description, images and prices, edited live. */
export function StoreSection({ product }: { product: ProductDetail }) {
  const { t } = useTranslation("stores");
  return <p className="text-sm text-muted-foreground">{t("product.empty")}</p>;
}
