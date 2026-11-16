import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import type { MaterialDetail, MaterialRead } from "@/api/generated/hoard.schemas";
import { getReadMaterialQueryKey } from "@/api/generated/materials/materials";
import { useSupplierNames } from "@/features/contacts/SupplierPicker";
import {
  DeclarationsPanel,
  type Own,
  OwnDeclaration,
} from "@/features/declarations/DeclarationsPanel";
import { DocumentsSection } from "@/features/documents/DocumentsSection";
import { builtin, type HoardKind, shapeSpecs, typeField, without } from "@/features/fields/records";
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
import { useSettingsNames } from "@/features/settings/names";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { at } from "@/ui/layouts/detailLayout";

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

const UnitWeight = ({ item }: Shown) => {
  const { t } = useTranslation("catalog");
  const quantity = useQuantity();
  const names = useSettingsNames();
  return (
    item.unit_weight !== null &&
    t("materials.unitWeightIs", {
      unit: names.unit(item.tracking_unit_id),
      weight: quantity(item.unit_weight),
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

/** Reads a material again once something on its page changed it. */
const useRefetch = (item: MaterialDetail) => {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: getReadMaterialQueryKey(item.id) });
};

const Stock = ({ item }: { item: MaterialDetail }) => (
  <StockTable
    stock={item.stock}
    unit={useSettingsNames().unit(item.tracking_unit_id)}
    bins={{ itemId: item.id, onChanged: useRefetch(item) }}
  />
);

const Picture = ({ item }: { item: MaterialDetail }) => (
  <ItemPicture item={item} onChanged={useRefetch(item)} />
);

const field = (id: string) => ({ type: "field", props: { field: id } });

/** What a material records of its own and declares, each a field of its page. */
const own = (id: Own, label: string) =>
  builtin<MaterialDetail>(
    id,
    label,
    (material) => material.id,
    ({ item }) => <OwnDeclaration of="material" id={item.id} own={id} />
  );

/** A material's page as the Ingredient shape draws it: everything a material
 *  has, which every shape knows. */
const INGREDIENT = {
  header: [],
  main: [
    at(1, {
      type: "section",
      children: [
        "on_hand",
        "last_stocktake",
        "low_stock_limit",
        "sku",
        "category",
        "type",
        "tracking_unit",
        "purchase_unit",
        "unit_weight",
        "ingredient_name",
        "allergens",
        "fragrance_allergens",
        "supplier",
        "supplier_code",
        "average_cost",
      ].map(field),
    }),
    at(3, field("picture")),
    at(4, field("notes")),
    at(5, field("stock")),
    at(6, field("lots")),
    at(7, field("declarations")),
    at(8, field("runs")),
    at(9, field("adjustments")),
    at(10, field("stocktakes")),
    at(11, field("documents")),
    at(12, { type: "byline" }),
  ],
  side: [at(2, { type: "section", children: [{ type: "properties" }] })],
};

/** A material's pages by the shape of its type: Packaging, such as tins,
 *  cards and labels, without what an ingredient declares. */
const SHAPES = shapeSpecs("material", INGREDIENT, {
  ingredient: INGREDIENT,
  packaging: without(INGREDIENT, [
    "ingredient_name",
    "allergens",
    "fragrance_allergens",
    "unit_weight",
    "declarations",
  ]),
});

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
      "status",
      "catalog:items.status",
      (material) => material.status,
      ({ item }) => <StatusBadge status={item.status} />
    ),
    builtin(
      "sku",
      "hoard:common.sku",
      (material) => material.sku,
      ({ item }) => item.sku
    ),
    builtin("category", "hoard:common.category", (material) => material.category_id, Category),
    typeField(),
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
      "unit_weight",
      "catalog:materials.unitWeight",
      (material) => material.unit_weight,
      UnitWeight
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
    builtin(
      "last_stocktake",
      "catalog:items.lastStocktake",
      (material) => material.last_stocktake,
      LastStocktake
    ),
    builtin("picture", "catalog:items.picture", (material) => material.picture_id, Picture),
    builtin(
      "notes",
      "hoard:common.notes",
      (material) => material.notes,
      ({ item }) => item.notes && <p className="text-sm whitespace-pre-line">{item.notes}</p>
    ),
    own("ingredient_name", "declarations:own.ingredient_name"),
    own("allergens", "declarations:own.allergens"),
    own("fragrance_allergens", "declarations:own.fragrance_allergens"),
    builtin("stock", "catalog:items.stock", (material) => material.id, Stock),
    builtin(
      "lots",
      "catalog:items.lots",
      (material) => material.id,
      ({ item }) => <LotsTable lots={item.lots} />
    ),
    builtin(
      "runs",
      "catalog:history.runs",
      (material) => material.id,
      ({ item }) => <RunsHistory itemId={item.id} />
    ),
    builtin(
      "adjustments",
      "catalog:history.adjustments",
      (material) => material.id,
      ({ item }) => <AdjustmentsHistory itemId={item.id} />
    ),
    builtin(
      "stocktakes",
      "catalog:history.stocktakes",
      (material) => material.id,
      ({ item }) => <StocktakesHistory itemId={item.id} />
    ),
    builtin(
      "declarations",
      "declarations:panel.title",
      (material) => material.id,
      ({ item }) => <DeclarationsPanel of="material" id={item.id} />
    ),
    builtin(
      "documents",
      "documents:section.title",
      (material) => material.id,
      ({ item }) => <DocumentsSection of="material" id={item.id} />
    ),
  ],
  columns: [
    "name",
    "status",
    "sku",
    "category",
    "type",
    "on_hand",
    "low_stock_limit",
    "average_cost",
  ],
  costs: new Set(["average_cost"]),
  spec: SHAPES.ingredient,
  shapes: SHAPES,
  parts: { byline: (_node, item) => <RecordStamps record={item} /> },
  href: (material) => `/stock/materials/${material.id}`,
};
