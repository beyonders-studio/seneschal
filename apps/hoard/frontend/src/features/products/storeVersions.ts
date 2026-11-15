import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { useTranslation } from "react-i18next";

import type { ProductDetail, ProductWrite, StoreVersion } from "@/api/generated/hoard.schemas";
import { getReadProductQueryKey } from "@/api/generated/products/products";
import { getReadStoreProductQueryKey } from "@/api/generated/stores/stores";

/** The versions of the synced stores' products listing `product` that an
 *  edit of it starts from. */
export function storeVersions(product: ProductDetail): StoreVersion[] {
  return product.store_products.map(({ store_id, product_id, updated_at }) => ({
    store_id,
    product_id,
    updated_at,
  }));
}

/** `product` as it is, to send with a change of some of it. */
export function productWrite(product: ProductDetail, seesCosts: boolean): ProductWrite {
  return {
    name: product.name,
    status: product.status,
    category_id: product.category_id,
    low_stock_limit: product.low_stock_limit,
    cure_days: product.cure_days,
    traceability_list: product.traceability_list,
    rinse_off: product.rinse_off,
    ...(seesCosts ? { default_price: product.default_price } : {}),
    store_versions: storeVersions(product),
  };
}

/** Whether Hoard refused an edit because a store product it writes to changed
 *  in the store since the edit started. */
export function changedInStore(error: unknown): boolean {
  return axios.isAxiosError(error) && error.response?.data?.detail === "STORE_PRODUCT_CHANGED";
}

/** The stores listing `product`, by their channel's name, such as "Shopify". */
export function useListedIn(): (product: ProductDetail) => string {
  const { t } = useTranslation("stores");
  return (product) =>
    [...new Set(product.store_products.map((held) => t(`channels.${held.channel}`)))].join(", ");
}

/** Reads `product` and its store products again, after an edit wrote to the
 *  stores or found that one changed there. */
export function useRefreshStoreProducts(): (product: ProductDetail) => Promise<void> {
  const queryClient = useQueryClient();
  return async (product) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: getReadProductQueryKey(product.id) }),
      ...product.store_products.map((held) =>
        queryClient.invalidateQueries({
          queryKey: getReadStoreProductQueryKey(held.store_id, held.product_id),
        })
      ),
    ]);
  };
}
