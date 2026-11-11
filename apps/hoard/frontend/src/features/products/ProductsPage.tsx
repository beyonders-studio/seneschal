import { useNavigate } from "@tanstack/react-router";
import { Package } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useListProducts } from "@/api/generated/products/products";
import { LayoutsButton, RecordTable } from "@/features/fields/records";
import { ItemList, useItemSearch } from "@/features/items/ItemList";
import { PRODUCTS } from "@/features/products/fields";
import { ProductDialog } from "@/features/products/ProductDialog";

/** The products list, under Stock. */
export function ProductsPage() {
  const { t } = useTranslation("catalog");
  const navigate = useNavigate();
  const search = useItemSearch();
  const products = useListProducts(search.params);
  const [adding, setAdding] = useState(false);

  return (
    <>
      <ItemList
        search={search}
        total={products.data?.total}
        addLabel={t("products.add")}
        onAdd={() => setAdding(true)}
        empty={{
          icon: <Package />,
          title: t("products.emptyTitle"),
          description: t("products.emptyBody"),
        }}
        failed={products.isError}
        onRetry={() => void products.refetch()}
        more={<LayoutsButton of={PRODUCTS} rows={products.data?.items ?? []} />}
      >
        <RecordTable of={PRODUCTS} items={products.data?.items ?? []} />
        {products.data?.items.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("items.noMatch")}</p>
        )}
      </ItemList>
      <ProductDialog
        open={adding}
        onOpenChange={setAdding}
        onSaved={(product) =>
          void navigate({
            to: "/stock/products/$productId",
            params: { productId: String(product.id) },
          })
        }
      />
    </>
  );
}
