import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  deleteProduct,
  getListProductsQueryKey,
  useReadProduct,
} from "@/api/generated/products/products";
import { LayoutsButton, RecordDetail } from "@/features/fields/records";
import { isLow } from "@/features/items/format";
import { ItemHeader } from "@/features/items/ItemHeader";
import { LowStockBadge } from "@/features/items/ItemList";
import { PRODUCTS } from "@/features/products/fields";
import { ProductDialog } from "@/features/products/ProductDialog";
import { StatusBadge } from "@/features/products/variations";
import { errorMessage } from "@/lib/errors";
import { StatusMessage } from "@/ui/status-message";

/**
 * One product, under Stock: what it is, its pictures, and its variations
 * with their stock and lots at each location.
 */
export function ProductPage({ productId }: { productId: number }) {
  const { t } = useTranslation("catalog");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: product, error, refetch } = useReadProduct(productId);
  const [editing, setEditing] = useState(false);

  if (error)
    return (
      <StatusMessage
        title={errorMessage(error, t, "products.loadFailed")}
        onRetry={() => void refetch()}
      />
    );
  if (!product) return <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;

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
      />
      <RecordDetail of={PRODUCTS} item={product} onSaved={() => void refetch()} />
      <ProductDialog product={product} open={editing} onOpenChange={setEditing} />
    </div>
  );
}
