import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { ProductVariationRead } from "@/api/generated/hoard.schemas";
import {
  deleteProduct,
  deleteProductVariation,
  getListProductsQueryKey,
  getReadProductQueryKey,
  useReadProduct,
} from "@/api/generated/products/products";
import { toast } from "@/components/Toaster";
import { isLow, useQuantity } from "@/features/items/format";
import { ItemSection, LotsTable, RecordStamps, StockTable } from "@/features/items/Holdings";
import { ItemFacts, ItemHeader } from "@/features/items/ItemHeader";
import { LowStockBadge } from "@/features/items/ItemList";
import { ProductDialog } from "@/features/products/ProductDialog";
import { ProductImages } from "@/features/products/ProductImages";
import { VariationDialog } from "@/features/products/VariationDialog";
import { StatusBadge, useVariationName } from "@/features/products/variations";
import { useSettingsNames } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { useSession } from "@/session";
import { Button } from "@/ui/button";
import { ConfirmDialog } from "@/ui/confirm-dialog";
import { DropdownMenuItem } from "@/ui/dropdown-menu";
import { RowActionsMenu } from "@/ui/row-actions-menu";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

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
  const names = useSettingsNames();
  const quantity = useQuantity();
  const money = useMoney();
  const seesCosts = useSeesCosts();
  const variationName = useVariationName();

  if (error)
    return (
      <StatusMessage
        title={errorMessage(error, t, "products.loadFailed")}
        onRetry={() => void refetch()}
      />
    );
  if (!product) return <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;

  // A simple product's one variation needs no name beside its stock.
  const named = product.variations.length > 1;
  const ofVariation = (variation: ProductVariationRead) =>
    named ? variationName(variation) : undefined;
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
      />
      <ItemFacts
        facts={[
          [t("hoard:common.onHand"), quantity(product.on_hand)],
          [
            t("hoard:common.lowStockLimit"),
            product.low_stock_limit && quantity(product.low_stock_limit),
          ],
          [t("hoard:common.category"), names.category(product.category_id)],
          [
            t("products.defaultPrice"),
            seesCosts && product.default_price && money(product.default_price),
          ],
        ]}
      />
      <ItemSection title={t("products.images")}>
        <ProductImages productId={productId} name={product.name} />
      </ItemSection>
      <ItemSection title={t("products.variations")}>
        <Variations productId={productId} variations={product.variations} />
      </ItemSection>
      <ItemSection title={t("items.stock")}>
        <StockTable
          stock={product.variations.flatMap((variation) =>
            variation.stock.map((row) => ({ ...row, variation: ofVariation(variation) }))
          )}
        />
      </ItemSection>
      <ItemSection title={t("items.lots")}>
        <LotsTable
          lots={product.variations.flatMap((variation) =>
            variation.lots.map((lot) => ({ ...lot, variation: ofVariation(variation) }))
          )}
        />
      </ItemSection>
      <RecordStamps record={product} />
      <ProductDialog product={product} open={editing} onOpenChange={setEditing} />
    </div>
  );
}

/** A product's variations, each with what is on hand and, for a member who
 *  sees costs, its price and average cost. */
function Variations({
  productId,
  variations,
}: {
  productId: number;
  variations: ProductVariationRead[];
}) {
  const { t } = useTranslation("catalog");
  const { read_only } = useSession();
  const queryClient = useQueryClient();
  const quantity = useQuantity();
  const money = useMoney();
  const seesCosts = useSeesCosts();
  const variationName = useVariationName();
  const [editing, setEditing] = useState<ProductVariationRead | "new" | null>(null);
  const [removing, setRemoving] = useState<ProductVariationRead | null>(null);
  const remove = useMutation({
    mutationFn: (variationId: number) => deleteProductVariation(productId, variationId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: getReadProductQueryKey(productId) }),
    onError: (error) => toast.error(errorMessage(error, t, "items.removeFailed")),
  });

  return (
    <div className="flex flex-col items-start gap-2">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("products.variation")}</TableHead>
            <TableHead>{t("hoard:common.sku")}</TableHead>
            <TableHead className="text-right">{t("hoard:common.onHand")}</TableHead>
            {seesCosts && <TableHead className="text-right">{t("hoard:common.price")}</TableHead>}
            {seesCosts && <TableHead className="text-right">{t("items.averageCost")}</TableHead>}
            <TableHead className="w-12" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {variations.map((variation) => (
            <TableRow key={variation.id}>
              <TableCell>{variationName(variation)}</TableCell>
              <TableCell>{variation.sku}</TableCell>
              <TableCell className="text-right tabular-nums">
                {quantity(variation.on_hand)}
              </TableCell>
              {seesCosts && (
                <TableCell className="text-right tabular-nums">
                  {variation.price ? money(variation.price) : t("products.usesDefaultPrice")}
                </TableCell>
              )}
              {seesCosts && (
                <TableCell className="text-right tabular-nums">
                  {variation.average_cost && money(variation.average_cost, { exact: true })}
                </TableCell>
              )}
              <TableCell>
                <RowActionsMenu subject={variationName(variation)}>
                  {!read_only && (
                    <DropdownMenuItem onSelect={() => setEditing(variation)}>
                      {t("hoard:common.edit")}
                    </DropdownMenuItem>
                  )}
                  {!read_only && variations.length > 1 && (
                    <DropdownMenuItem
                      className="text-destructive"
                      onSelect={() => setRemoving(variation)}
                    >
                      {t("hoard:common.delete")}
                    </DropdownMenuItem>
                  )}
                </RowActionsMenu>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {!read_only && (
        <Button variant="outline" size="sm" onClick={() => setEditing("new")}>
          <Plus aria-hidden />
          {t("products.addVariation")}
        </Button>
      )}
      <VariationDialog
        productId={productId}
        variation={editing === "new" || editing === null ? undefined : editing}
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
      />
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={t("items.removeTitle", { name: removing ? variationName(removing) : "" })}
        description={t("items.removeBody")}
        confirmLabel={t("hoard:common.delete")}
        destructive
        onConfirm={() => removing && remove.mutate(removing.id)}
      />
    </div>
  );
}
