import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  ProductDetail,
  ProductRead,
  ProductVariationRead,
} from "@/api/generated/hoard.schemas";
import { deleteProductVariation, getReadProductQueryKey } from "@/api/generated/products/products";
import { toast } from "@/components/Toaster";
import { AutoManufactureField } from "@/features/automanufacture/AutoManufactureField";
import {
  DeclarationsPanel,
  type Own,
  OwnDeclaration,
} from "@/features/declarations/DeclarationsPanel";
import { DocumentsSection } from "@/features/documents/DocumentsSection";
import { builtin, type HoardKind, shapeSpecs, typeField, without } from "@/features/fields/records";
import { CureDays } from "@/features/items/CureDaysField";
import { isLow, useQuantity } from "@/features/items/format";
import {
  AdjustmentsHistory,
  OrdersHistory,
  PurchasesHistory,
  RunsHistory,
  StocktakesHistory,
} from "@/features/items/History";
import { RecordStamps, StockTable } from "@/features/items/Holdings";
import { IdSelect } from "@/features/items/IdSelect";
import { LowStockBadge, StatusBadge } from "@/features/items/ItemList";
import { LotsTable } from "@/features/items/lots";
import { UsageTable, UsedInTable } from "@/features/items/usage";
import { Glance, Profitability } from "@/features/products/glance";
import { ProductImages } from "@/features/products/ProductImages";
import { PricingPanel } from "@/features/products/pricing";
import { VariationDialog } from "@/features/products/VariationDialog";
import { useVariationName } from "@/features/products/variations";
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
import { at } from "@/ui/layouts/detailLayout";
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

const YesNo = ({ value }: { value: boolean }) => {
  const { t } = useTranslation();
  return value ? t("common.yes") : t("common.no");
};

const DefaultPrice = ({ item }: Shown) => {
  const money = useMoney();
  return useSeesCosts() && item.default_price && money(item.default_price);
};

/** A simple product's one variation needs no name beside its stock or lots. */
const named = (product: ProductDetail) => product.variations.length > 1;

const Stock = ({ item }: { item: ProductDetail }) => {
  const variationName = useVariationName();
  const queryClient = useQueryClient();
  const name = (variation: ProductDetail["variations"][number]) =>
    named(item) ? variationName(variation) : undefined;
  return (
    <StockTable
      stock={item.variations.flatMap((variation) =>
        variation.stock.map((row) => ({ ...row, variation: name(variation) }))
      )}
      moves={{
        items: item.variations.map((variation) => ({ ...variation, name: name(variation) })),
        onChanged: () =>
          void queryClient.invalidateQueries({ queryKey: getReadProductQueryKey(item.id) }),
      }}
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

/** What `children` shows of one of a product's variations: of its only one,
 *  or of the one chosen among them. */
function ByVariation({
  product,
  children,
}: {
  product: ProductDetail;
  children: (variationId: number) => ReactNode;
}) {
  const { t } = useTranslation("catalog");
  const variationName = useVariationName();
  const { variations } = product;
  const [chosen, setChosen] = useState(variations[0]?.id ?? null);
  const shown = variations.find((variation) => variation.id === chosen) ?? variations[0];
  if (!shown) return null;
  if (variations.length === 1) return children(shown.id);
  return (
    <div className="flex flex-col gap-2">
      <div className="w-56">
        <IdSelect
          aria-label={t("products.variation")}
          value={shown.id}
          options={variations.map((variation) => ({
            id: variation.id,
            name: variationName(variation),
          }))}
          onChange={(id) => id !== null && setChosen(id)}
        />
      </div>
      {children(shown.id)}
    </div>
  );
}

/** Each variation's part of the page, under its name where it has one. */
function EachVariation({
  product,
  children,
}: {
  product: ProductDetail;
  children: (variation: ProductVariationRead) => ReactNode;
}) {
  const variationName = useVariationName();
  if (!named(product)) return product.variations.map((variation) => children(variation));
  return (
    <div className="flex flex-col gap-3">
      {product.variations.map((variation) => (
        <div key={variation.id} className="flex flex-col gap-1">
          <h4 className="text-sm font-medium">{variationName(variation)}</h4>
          {children(variation)}
        </div>
      ))}
    </div>
  );
}

/** A history section of a product: of one of its variations at a time. */
const history = (id: string, label: string, Show: (props: { itemId: number }) => ReactNode) =>
  builtin<ProductDetail>(
    id,
    label,
    (product) => product.id,
    ({ item }) => <ByVariation product={item}>{(itemId) => <Show itemId={itemId} />}</ByVariation>
  );

const field = (id: string) => ({ type: "field", props: { field: id } });

/** What a product records of its own, each a field of its page: what it
 *  declares while it has no recipe. */
const OWN: Own[] = ["ingredient_name", "allergens", "fragrance_allergens"];

const own = (id: Own) =>
  builtin<ProductDetail>(
    id,
    `declarations:own.${id}`,
    (product) => product.id,
    ({ item }) => <OwnDeclaration of="product" id={item.id} own={id} />
  );

/** Everything a product's page can place, which every shape knows: what it
 *  records of its own follows the declaration it rolls up. */
const EVERYTHING = {
  header: [],
  main: [
    at(1, field("glance")),
    at(2, {
      type: "section",
      children: [
        "low_stock_limit",
        "category",
        "type",
        "default_price",
        "cure_days",
        "traceability_list",
        "rinse_off",
      ].map(field),
    }),
    at(4, field("profit")),
    at(5, field("pricing")),
    at(6, field("images")),
    at(7, field("variations")),
    at(8, field("recipe")),
    at(9, field("stock")),
    at(10, field("lots")),
    at(11, field("usage")),
    at(12, field("used_in")),
    at(13, field("declarations")),
    at(14, { type: "section", children: OWN.map(field) }),
    at(15, field("runs")),
    at(16, field("orders")),
    at(17, field("purchases")),
    at(18, field("adjustments")),
    at(19, field("stocktakes")),
    at(20, field("documents")),
    at(21, field("stores")),
    at(22, field("auto_manufacture")),
    at(23, { type: "byline" }),
  ],
  side: [at(3, { type: "section", children: [{ type: "properties" }] })],
};

/** Without them, an empty section stays out of the shapes that hide them. */
const MADE = (() => {
  const shown = without(EVERYTHING, OWN);
  return { ...shown, main: shown.main.filter((node) => node.children?.length !== 0) };
})();

/** A product's pages by the shape of its type: Made, by its recipe; Bought
 *  finished, its recipe and runs hidden as well; and a Bundle, which holds no
 *  stock or lots of its own, and is neither made, bought nor used. */
const SHAPES = shapeSpecs("product", EVERYTHING, {
  made: MADE,
  bought: without(MADE, ["recipe", "runs"]),
  bundle: without(MADE, [
    "stock",
    "lots",
    "usage",
    "used_in",
    "runs",
    "purchases",
    "adjustments",
    "stocktakes",
  ]),
});

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
      "catalog:items.status",
      (product) => product.status,
      ({ item }) => <StatusBadge status={item.status} />
    ),
    builtin("category", "hoard:common.category", (product) => product.category_id, Category),
    typeField(),
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
    builtin(
      "cure_days",
      "catalog:items.cureDays",
      (product) => product.cure_days,
      ({ item }) => <CureDays days={item.cure_days} />
    ),
    builtin(
      "traceability_list",
      "catalog:products.traceabilityList",
      (product) => product.traceability_list,
      ({ item }) => <YesNo value={item.traceability_list} />
    ),
    builtin(
      "rinse_off",
      "catalog:products.rinseOff",
      (product) => product.rinse_off,
      ({ item }) => <YesNo value={item.rinse_off} />
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
      ({ item }) => <Variations product={item} />
    ),
    builtin(
      "glance",
      "catalog:products.glance.title",
      (product) => product.id,
      ({ item }) => <Glance product={item} />
    ),
    builtin(
      "profit",
      "catalog:products.profit.title",
      (product) => product.totals.profit,
      ({ item }) => <Profitability product={item} />
    ),
    builtin(
      "pricing",
      "catalog:products.pricing.title",
      (product) => product.id,
      ({ item }) => <PricingPanel product={item} />
    ),
    builtin("stock", "catalog:items.stock", (product) => product.id, Stock),
    builtin(
      "usage",
      "catalog:items.usage.title",
      (product) => product.id,
      ({ item }) => (
        <EachVariation product={item}>
          {(variation) => <UsageTable key={variation.id} usage={variation.usage} unit="" />}
        </EachVariation>
      )
    ),
    builtin(
      "used_in",
      "catalog:items.usedIn.title",
      (product) => product.id,
      ({ item }) => (
        <EachVariation product={item}>
          {(variation) => <UsedInTable key={variation.id} usedIn={variation.used_in} />}
        </EachVariation>
      )
    ),
    history("runs", "catalog:history.runs", ({ itemId }) => <RunsHistory itemId={itemId} made />),
    history("orders", "catalog:history.orders", OrdersHistory),
    history("purchases", "catalog:history.purchases", PurchasesHistory),
    history("adjustments", "catalog:history.adjustments", AdjustmentsHistory),
    history("stocktakes", "catalog:history.stocktakes", StocktakesHistory),
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
    ...OWN.map(own),
    builtin(
      "documents",
      "documents:section.title",
      (product) => product.id,
      ({ item }) => <DocumentsSection of="product" id={item.id} />
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
  columns: ["name", "status", "category", "type", "on_hand", "low_stock_limit", "default_price"],
  costs: new Set(["default_price", "profit", "pricing"]),
  spec: SHAPES.made,
  shapes: SHAPES,
  parts: { byline: (_node, item) => <RecordStamps record={item} /> },
  href: (product) => `/stock/products/${product.id}`,
};

/** A product's variations, each with what is on hand and, for a member who
 *  sees costs, its price and average cost. */
function Variations({ product }: { product: ProductDetail }) {
  const { id: productId, variations } = product;
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
            <TableHead className="text-right">{t("products.glance.available")}</TableHead>
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
              <TableCell className="text-right tabular-nums">
                {quantity(variation.available)}
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
        product={product}
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
