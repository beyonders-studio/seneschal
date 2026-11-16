import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ItemStatus } from "@/api/generated/hoard.schemas";
import {
  deleteProduct,
  getListProductsQueryKey,
  updateProduct,
  useReadProduct,
} from "@/api/generated/products/products";
import { LayoutsButton, RecordDetail } from "@/features/fields/records";
import { isLow } from "@/features/items/format";
import { ItemHeader } from "@/features/items/ItemHeader";
import { LowStockBadge, StatusBadge } from "@/features/items/ItemList";
import { PRODUCTS } from "@/features/products/fields";
import { ProductDialog } from "@/features/products/ProductDialog";
import {
  productWrite,
  useListedIn,
  useRefreshStoreProducts,
} from "@/features/products/storeVersions";
import { errorMessage } from "@/lib/errors";
import { useSeesCosts } from "@/lib/permissions";
import { StatusMessage } from "@/ui/status-message";

/**
 * One product, under Stock: what it is, its pictures, and its variations
 * with their stock and lots at each location. One a synced store lists is
 * not deleted here: archiving it is offered instead.
 */
export function ProductPage({ productId }: { productId: number }) {
  const { t } = useTranslation("catalog");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: product, error, refetch } = useReadProduct(productId);
  const [editing, setEditing] = useState(false);
  const seesCosts = useSeesCosts();
  const listedIn = useListedIn();
  const refresh = useRefreshStoreProducts();

  if (error)
    return (
      <StatusMessage
        title={errorMessage(error, t, "products.loadFailed")}
        onRetry={() => void refetch()}
      />
    );
  if (!product) return <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;

  const archived = product.status === ItemStatus.archived;
  const archive = async () => {
    try {
      await updateProduct(product.id, {
        ...productWrite(product, seesCosts),
        status: ItemStatus.archived,
      });
    } finally {
      void queryClient.invalidateQueries({ queryKey: getListProductsQueryKey() });
      await refresh(product);
    }
  };
  const store = listedIn(product);

  return (
    <div className="flex flex-col gap-6">
      <ItemHeader
        back={{ to: "/stock/products", label: t("hoard:stock.products") }}
        title={product.name}
        marks={
          <>
            <StatusBadge status={product.status} />
            {isLow(product.on_hand, product.low_stock_limit) && <LowStockBadge />}
          </>
        }
        onEdit={() => setEditing(true)}
        remove={() => deleteProduct(productId)}
        onRemoved={() => {
          void queryClient.invalidateQueries({ queryKey: getListProductsQueryKey() });
          void navigate({ to: "/stock/products" });
        }}
        more={<LayoutsButton of={PRODUCTS} rows={[product]} page={product} />}
        kept={
          product.store_products.length > 0
            ? {
                reason: archived
                  ? t("products.listedArchived", { store })
                  : t("products.listed", { store }),
                offer: archived ? undefined : { label: t("products.archive"), run: archive },
              }
            : undefined
        }
      />
      <RecordDetail of={PRODUCTS} item={product} onSaved={() => void refetch()} />
      <ProductDialog product={product} open={editing} onOpenChange={setEditing} />
    </div>
  );
}
