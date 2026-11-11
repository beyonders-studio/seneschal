import { Link, useNavigate } from "@tanstack/react-router";
import { Package } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useListProducts } from "@/api/generated/products/products";
import { isLow, useQuantity } from "@/features/items/format";
import { ItemList, LowStockBadge, useItemSearch } from "@/features/items/ItemList";
import { ProductDialog } from "@/features/products/ProductDialog";
import { StatusBadge } from "@/features/products/variations";
import { useSettingsNames } from "@/features/settings/names";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** The products list, under Stock. */
export function ProductsPage() {
  const { t } = useTranslation("catalog");
  const navigate = useNavigate();
  const search = useItemSearch();
  const products = useListProducts(search.params);
  const [adding, setAdding] = useState(false);
  const names = useSettingsNames();
  const quantity = useQuantity();
  const money = useMoney();
  const seesCosts = useSeesCosts();

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
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("hoard:common.name")}</TableHead>
              <TableHead>{t("hoard:common.category")}</TableHead>
              <TableHead className="text-right">{t("hoard:common.onHand")}</TableHead>
              <TableHead className="text-right">{t("hoard:common.lowStockLimit")}</TableHead>
              {seesCosts && (
                <TableHead className="text-right">{t("products.defaultPrice")}</TableHead>
              )}
            </TableRow>
          </TableHeader>
          <TableBody>
            {products.data?.items.map((product) => (
              <TableRow key={product.id}>
                <TableCell>
                  <span className="inline-flex items-center gap-2">
                    <Link
                      to="/stock/products/$productId"
                      params={{ productId: String(product.id) }}
                      className="font-medium hover:underline"
                    >
                      {product.name}
                    </Link>
                    <StatusBadge status={product.status} />
                  </span>
                </TableCell>
                <TableCell>{names.category(product.category_id)}</TableCell>
                <TableCell className="text-right tabular-nums">
                  <span className="inline-flex items-center gap-2">
                    {isLow(product.on_hand, product.low_stock_limit) && <LowStockBadge />}
                    {quantity(product.on_hand)}
                  </span>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {product.low_stock_limit && quantity(product.low_stock_limit)}
                </TableCell>
                {seesCosts && (
                  <TableCell className="text-right tabular-nums">
                    {product.default_price && money(product.default_price)}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
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
