import { useTranslation } from "react-i18next";

import type { MaterialDetail, MaterialRead } from "@/api/generated/hoard.schemas";
import { useSupplierNames } from "@/features/contacts/SupplierPicker";
import { builtin, type HoardKind } from "@/features/fields/records";
import { isLow, useQuantity } from "@/features/items/format";
import { RecordStamps, StockTable } from "@/features/items/Holdings";
import { LowStockBadge } from "@/features/items/ItemList";
import { LotsTable } from "@/features/items/lots";
import { useSettingsNames } from "@/features/settings/names";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { at, detailLayoutSpec } from "@/ui/layouts/detailLayout";

type Shown = { item: MaterialRead };

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

const PurchaseUnit = ({ item }: Shown) => {
  const { t } = useTranslation("catalog");
  const names = useSettingsNames();
  const quantity = useQuantity();
  return (
    item.purchase_unit_id !== null &&
    t("materials.purchaseUnitIs", {
      unit: names.unit(item.purchase_unit_id),
      conversion: quantity(item.purchase_conversion),
      trackingUnit: names.unit(item.tracking_unit_id),
    })
  );
};

const Supplier = ({ item }: Shown) => useSupplierNames()(item.supplier_contact_id);

const AverageCost = ({ item }: Shown) => {
  const money = useMoney();
  return (
    useSeesCosts() &&
    item.average_cost && (
      <span className="tabular-nums">{money(item.average_cost, { exact: true })}</span>
    )
  );
};

const Stock = ({ item }: { item: MaterialDetail }) => (
  <StockTable stock={item.stock} unit={useSettingsNames().unit(item.tracking_unit_id)} />
);

const field = (id: string) => ({ type: "field", props: { field: id } });

/** Materials, as their table lists them and a material's page shows one. */
export const MATERIALS: HoardKind<MaterialRead, MaterialDetail> = {
  kind: "material",
  fields: [
    {
      id: "name",
      kind: "title",
      label: "hoard:common.name",
      source: "builtin",
      hideable: false,
      requireable: false,
      value: (material) => material.name,
    },
    builtin(
      "sku",
      "hoard:common.sku",
      (material) => material.sku,
      ({ item }) => item.sku
    ),
    builtin("category", "hoard:common.category", (material) => material.category_id, Category),
    builtin("on_hand", "hoard:common.onHand", (material) => material.on_hand, OnHand),
    builtin(
      "low_stock_limit",
      "hoard:common.lowStockLimit",
      (material) => material.low_stock_limit,
      LowStockLimit
    ),
    builtin(
      "tracking_unit",
      "catalog:materials.trackingUnit",
      (material) => material.tracking_unit_id,
      TrackingUnit
    ),
    builtin(
      "purchase_unit",
      "catalog:materials.purchaseUnit",
      (material) => material.purchase_unit_id,
      PurchaseUnit
    ),
    builtin(
      "supplier",
      "catalog:materials.supplier",
      (material) => material.supplier_contact_id,
      Supplier
    ),
    builtin(
      "supplier_code",
      "catalog:materials.supplierCode",
      (material) => material.supplier_code,
      ({ item }) => item.supplier_code
    ),
    builtin(
      "average_cost",
      "catalog:items.averageCost",
      (material) => material.average_cost,
      AverageCost
    ),
  ],
  pageFields: [
    builtin("stock", "catalog:items.stock", (material) => material.id, Stock),
    builtin(
      "lots",
      "catalog:items.lots",
      (material) => material.id,
      ({ item }) => <LotsTable lots={item.lots} />
    ),
  ],
  columns: ["name", "sku", "category", "on_hand", "low_stock_limit", "average_cost"],
  costs: new Set(["average_cost"]),
  spec: detailLayoutSpec({
    kind: "material",
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
            "purchase_unit",
            "supplier",
            "supplier_code",
            "average_cost",
          ].map(field),
        }),
        at(3, field("stock")),
        at(4, field("lots")),
        at(5, { type: "byline" }),
      ],
      side: [at(2, { type: "section", children: [{ type: "properties" }] })],
    },
    fieldParts: ["field", "properties"],
  }),
  parts: { byline: (_node, item) => <RecordStamps record={item} /> },
  href: (material) => `/stock/materials/${material.id}`,
};
