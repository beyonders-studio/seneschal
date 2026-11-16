import { useQueryClient } from "@tanstack/react-query";

import { getReadBaseQueryKey } from "@/api/generated/bases/bases";
import type { BaseDetail, BaseRead } from "@/api/generated/hoard.schemas";
import { DocumentsSection } from "@/features/documents/DocumentsSection";
import { builtin, type HoardKind } from "@/features/fields/records";
import { CureDays } from "@/features/items/CureDaysField";
import { isLow, useQuantity } from "@/features/items/format";
import {
  AdjustmentsHistory,
  LastStocktake,
  RunsHistory,
  StocktakesHistory,
} from "@/features/items/History";
import { RecordStamps, StockTable } from "@/features/items/Holdings";
import { LowStockBadge, StatusBadge } from "@/features/items/ItemList";
import { ItemPicture } from "@/features/items/ItemPicture";
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

/** Reads a base again once something on its page changed it. */
const useRefetch = (item: BaseDetail) => {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: getReadBaseQueryKey(item.id) });
};

/** What its recipe could make from what is usable at every location together. */
const Potential = ({ item }: { item: BaseDetail }) => {
  const quantity = useQuantity();
  const unit = useSettingsNames().unit(item.tracking_unit_id);
  return (
    item.potential !== null && (
      <span className="tabular-nums">
        {quantity(item.potential)} {unit}
      </span>
    )
  );
};

const Made = ({ item }: { item: BaseDetail }) => {
  const quantity = useQuantity();
  const unit = useSettingsNames().unit(item.tracking_unit_id);
  return (
    <span className="tabular-nums">
      {quantity(item.made)} {unit}
    </span>
  );
};

const Stock = ({ item }: { item: BaseDetail }) => (
  <StockTable
    stock={item.stock}
    unit={useSettingsNames().unit(item.tracking_unit_id)}
    bins={{ itemId: item.id, onChanged: useRefetch(item) }}
  />
);

const Picture = ({ item }: { item: BaseDetail }) => (
  <ItemPicture item={item} onChanged={useRefetch(item)} />
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
      "status",
      "catalog:items.status",
      (base) => base.status,
      ({ item }) => <StatusBadge status={item.status} />
    ),
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
    builtin(
      "cure_days",
      "catalog:items.cureDays",
      (base) => base.cure_days,
      ({ item }) => <CureDays days={item.cure_days} />
    ),
  ],
  pageFields: [
    builtin("potential", "catalog:items.potential", (base) => base.potential, Potential),
    builtin("made", "catalog:items.made", (base) => base.made, Made),
    builtin(
      "last_stocktake",
      "catalog:items.lastStocktake",
      (base) => base.last_stocktake,
      LastStocktake
    ),
    builtin("picture", "catalog:items.picture", (base) => base.picture_id, Picture),
    builtin(
      "notes",
      "hoard:common.notes",
      (base) => base.notes,
      ({ item }) => item.notes && <p className="text-sm whitespace-pre-line">{item.notes}</p>
    ),
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
    builtin(
      "runs",
      "catalog:history.runs",
      (base) => base.id,
      ({ item }) => <RunsHistory itemId={item.id} made />
    ),
    builtin(
      "adjustments",
      "catalog:history.adjustments",
      (base) => base.id,
      ({ item }) => <AdjustmentsHistory itemId={item.id} />
    ),
    builtin(
      "stocktakes",
      "catalog:history.stocktakes",
      (base) => base.id,
      ({ item }) => <StocktakesHistory itemId={item.id} />
    ),
    builtin(
      "documents",
      "documents:section.title",
      (base) => base.id,
      ({ item }) => <DocumentsSection of="base" id={item.id} />
    ),
  ],
  columns: ["name", "status", "sku", "category", "on_hand", "low_stock_limit", "average_cost"],
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
            "potential",
            "made",
            "last_stocktake",
            "low_stock_limit",
            "sku",
            "category",
            "tracking_unit",
            "cure_days",
            "average_cost",
          ].map(field),
        }),
        at(3, field("picture")),
        at(4, field("notes")),
        at(5, field("recipe")),
        at(6, field("stock")),
        at(7, field("lots")),
        at(8, field("runs")),
        at(9, field("adjustments")),
        at(10, field("stocktakes")),
        at(11, field("documents")),
        at(12, { type: "byline" }),
      ],
      side: [at(2, { type: "section", children: [{ type: "properties" }] })],
    },
    fieldParts: ["field", "properties"],
  }),
  parts: { byline: (_node, item) => <RecordStamps record={item} /> },
  href: (base) => `/stock/bases/${base.id}`,
};
