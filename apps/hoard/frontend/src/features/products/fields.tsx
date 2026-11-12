import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  ProductDetail,
  ProductRead,
  ProductVariationRead,
} from "@/api/generated/hoard.schemas";
import { deleteProductVariation, getReadProductQueryKey } from "@/api/generated/products/products";
import { toast } from "@/components/Toaster";
import { AutoManufactureField } from "@/features/automanufacture/AutoManufactureField";
import { DeclarationsPanel } from "@/features/declarations/DeclarationsPanel";
import { builtin, type HoardKind } from "@/features/fields/records";
import { isLow, useQuantity } from "@/features/items/format";
import { RecordStamps, StockTable } from "@/features/items/Holdings";
import { LowStockBadge } from "@/features/items/ItemList";
import { LotsTable } from "@/features/items/lots";
import { ProductImages } from "@/features/products/ProductImages";
import { VariationDialog } from "@/features/products/VariationDialog";
import { StatusBadge, useVariationName } from "@/features/products/variations";
import { RecipeSection } from "@/features/recipes/RecipeSection";
import { useSettingsNames } from "@/features/settings/names";
import { StoreSection } from "@/features/stores/StoreSection";
import { errorMessage } from "@/lib/errors";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { useSession } from "@/session";
import { Button } from "@/ui/button";
import { ConfirmDialog } from "@/ui/confirm-dialog";
import { DropdownMenuItem } from "@/ui/dropdown-menu";
import { at, detailLayoutSpec } from "@/ui/layouts/detailLayout";
import { RowActionsMenu } from "@/ui/row-actions-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

type Shown = { item: ProductRead };

const OnHand = ({ item }: Shown) => {
  const quantity = useQuantity();
  return (
    <span className="inline-flex items-center gap-2 tabular-nums">
      {isLow(item.on_hand, item.low_stock_limit) && <LowStockBadge />}
      {quantity(item.on_hand)}
    </span>
  );
};

const LowStockLimit = ({ item }: Shown) => {
  const quantity = useQuantity();
  return (
    item.low_stock_limit && <span className="tabular-nums">{quantity(item.low_stock_limit)}</span>
  );
};

const Category = ({ item }: Shown) => useSettingsNames().category(item.category_id);

const DefaultPrice = ({ item }: Shown) => {
  const money = useMoney();
  return useSeesCosts() && item.default_price && money(item.default_price);
};

/** A simple product's one variation needs no name beside its stock or lots. */
const named = (product: ProductDetail) => product.variations.length > 1;

const Stock = ({ item }: { item: ProductDetail }) => {
  const variationName = useVariationName();
  return (
    <StockTable
      stock={item.variations.flatMap((variation) =>
        variation.stock.map((row) => ({
          ...row,
          variation: named(item) ? variationName(variation) : undefined,
        }))
      )}
    />
  );
};

/** Each variation's lots, under its name where it has one. */
const Lots = ({ item }: { item: ProductDetail }) => {
  const variationName = useVariationName();
  if (!named(item)) return <LotsTable lots={item.variations.flatMap((each) => each.lots)} />;
  return (
    <div className="flex flex-col gap-3">
      {item.variations.map((variation) => (
        <div key={variation.id} className="flex flex-col gap-1">
          <h4 className="text-sm font-medium">{variationName(variation)}</h4>
          <LotsTable lots={variation.lots} />
        </div>
      ))}
    </div>
  );
};

const field = (id: string) => ({ type: "field", props: { field: id } });

/** Products, as their table lists them and a product's page shows one. */
export const PRODUCTS: HoardKind<ProductRead, ProductDetail> = {
  kind: "product",
  fields: [
    {
      id: "name",
      kind: "title",
      label: "hoard:common.name",
      source: "builtin",
      hideable: false,
      requireable: false,
      value: (product) => product.name,
    },
    builtin(
      "status",
      "catalog:products.status",
      (product) => product.status,
      ({ item }) => <StatusBadge status={item.status} />
    ),
    builtin("category", "hoard:common.category", (product) => product.category_id, Category),
    builtin("on_hand", "hoard:common.onHand", (product) => product.on_hand, OnHand),
    builtin(
      "low_stock_limit",
      "hoard:common.lowStockLimit",
      (product) => product.low_stock_limit,
      LowStockLimit
    ),
    builtin(
      "default_price",
      "catalog:products.defaultPrice",
      (product) => product.default_price,
      DefaultPrice
    ),
  ],
  pageFields: [
    builtin(
      "images",
      "catalog:products.images",
      (product) => product.id,
      ({ item }) => <ProductImages productId={item.id} name={item.name} />
    ),
    builtin(
      "variations",
      "catalog:products.variations",
      (product) => product.id,
      ({ item }) => <Variations productId={item.id} variations={item.variations} />
    ),
    builtin("stock", "catalog:items.stock", (product) => product.id, Stock),
    builtin(
      "recipe",
      "recipes:section.title",
      (product) => product.id,
      ({ item }) => <RecipeSection product={item} />
    ),
    builtin("lots", "catalog:items.lots", (product) => product.id, Lots),
    builtin(
      "declarations",
      "declarations:panel.title",
      (product) => product.id,
      ({ item }) => <DeclarationsPanel of="product" id={item.id} />
    ),
    builtin(
      "stores",
      "stores:product.title",
      (product) => product.id,
      ({ item }) => <StoreSection product={item} />
    ),
    builtin(
      "auto_manufacture",
      "sync:autoManufacture.title",
      (product) => product.auto_manufacture,
      ({ item }) => <AutoManufactureField product={item} />
    ),
  ],
  columns: ["name", "status", "category", "on_hand", "low_stock_limit", "default_price"],
  costs: new Set(["default_price"]),
  spec: detailLayoutSpec({
    kind: "product",
    shipped: {
      header: [],
      main: [
        at(1, {
          type: "section",
          children: ["on_hand", "low_stock_limit", "category", "default_price"].map(field),
        }),
        at(3, field("images")),
        at(4, field("variations")),
        at(5, field("recipe")),
        at(6, field("stock")),
        at(7, field("lots")),
        at(8, field("declarations")),
        at(9, field("stores")),
        at(10, field("auto_manufacture")),
        at(11, { type: "byline" }),
      ],
      side: [at(2, { type: "section", children: [{ type: "properties" }] })],
    },
    fieldParts: ["field", "properties"],
  }),
  parts: { byline: (_node, item) => <RecordStamps record={item} /> },
  href: (product) => `/stock/products/${product.id}`,
};

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
