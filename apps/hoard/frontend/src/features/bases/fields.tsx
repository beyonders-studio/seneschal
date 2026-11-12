import type { BaseDetail, BaseRead } from "@/api/generated/hoard.schemas";
import { builtin, type HoardKind } from "@/features/fields/records";
import { isLow, useQuantity } from "@/features/items/format";
import { RecordStamps, StockTable } from "@/features/items/Holdings";
import { LowStockBadge } from "@/features/items/ItemList";
import { LotsTable } from "@/features/items/lots";
import { BaseRecipe } from "@/features/recipes/BaseRecipe";
import { useSettingsNames } from "@/features/settings/names";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { at, detailLayoutSpec } from "@/ui/layouts/detailLayout";

type Shown = { item: BaseRead };

const OnHand = ({ item }: Shown) => {
  const quantity = useQuantity();
  const unit = useSettingsNames().unit(item.tracking_unit_id);
  return (
    <span className="inline-flex items-center gap-2 tabular-nums">
      {isLow(item.on_hand, item.low_stock_limit) && <LowStockBadge />}
      {quantity(item.on_hand)} {unit}
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
const TrackingUnit = ({ item }: Shown) => useSettingsNames().unit(item.tracking_unit_id);

const AverageCost = ({ item }: Shown) => {
  const money = useMoney();
  return (
    useSeesCosts() &&
    item.average_cost && (
      <span className="tabular-nums">{money(item.average_cost, { exact: true })}</span>
    )
  );
};

const Stock = ({ item }: { item: BaseDetail }) => (
  <StockTable stock={item.stock} unit={useSettingsNames().unit(item.tracking_unit_id)} />
);

const field = (id: string) => ({ type: "field", props: { field: id } });

/** Bases, as their table lists them and a base's page shows one. */
export const BASES: HoardKind<BaseRead, BaseDetail> = {
  kind: "base",
  fields: [
    {
      id: "name",
      kind: "title",
      label: "hoard:common.name",
      source: "builtin",
      hideable: false,
      requireable: false,
      value: (base) => base.name,
    },
    builtin(
      "sku",
      "hoard:common.sku",
      (base) => base.sku,
      ({ item }) => item.sku
    ),
    builtin("category", "hoard:common.category", (base) => base.category_id, Category),
    builtin("on_hand", "hoard:common.onHand", (base) => base.on_hand, OnHand),
    builtin(
      "low_stock_limit",
      "hoard:common.lowStockLimit",
      (base) => base.low_stock_limit,
      LowStockLimit
    ),
    builtin(
      "tracking_unit",
      "catalog:materials.trackingUnit",
      (base) => base.tracking_unit_id,
      TrackingUnit
    ),
    builtin("average_cost", "catalog:items.averageCost", (base) => base.average_cost, AverageCost),
  ],
  pageFields: [
    builtin(
      "recipe",
      "recipes:section.title",
      (base) => base.id,
      ({ item }) => <BaseRecipe base={item} />
    ),
    builtin("stock", "catalog:items.stock", (base) => base.id, Stock),
    builtin(
      "lots",
      "catalog:items.lots",
      (base) => base.id,
      ({ item }) => <LotsTable lots={item.lots} />
    ),
  ],
  columns: ["name", "sku", "category", "on_hand", "low_stock_limit", "average_cost"],
  costs: new Set(["average_cost"]),
  spec: detailLayoutSpec({
    kind: "base",
    shipped: {
      header: [],
      main: [
        at(1, {
          type: "section",
          children: [
            "on_hand",
            "low_stock_limit",
            "sku",
            "category",
            "tracking_unit",
            "average_cost",
          ].map(field),
        }),
        at(3, field("recipe")),
        at(4, field("stock")),
        at(5, field("lots")),
        at(6, { type: "byline" }),
      ],
      side: [at(2, { type: "section", children: [{ type: "properties" }] })],
    },
    fieldParts: ["field", "properties"],
  }),
  parts: { byline: (_node, item) => <RecordStamps record={item} /> },
  href: (base) => `/stock/bases/${base.id}`,
};
