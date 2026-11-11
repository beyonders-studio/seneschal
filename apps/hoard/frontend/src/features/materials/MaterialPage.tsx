import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  deleteMaterial,
  getListMaterialsQueryKey,
  useReadMaterial,
} from "@/api/generated/materials/materials";
import { isLow, useQuantity } from "@/features/items/format";
import { ItemSection, LotsTable, RecordStamps, StockTable } from "@/features/items/Holdings";
import { ItemFacts, ItemHeader } from "@/features/items/ItemHeader";
import { LowStockBadge } from "@/features/items/ItemList";
import { MaterialDialog } from "@/features/materials/MaterialDialog";
import { useSettingsNames } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { Badge } from "@/ui/badge";
import { StatusMessage } from "@/ui/status-message";

/** One material, under Stock: what it is, and its stock and lots at each location. */
export function MaterialPage({ materialId }: { materialId: number }) {
  const { t } = useTranslation("catalog");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: material, error, refetch } = useReadMaterial(materialId);
  const [editing, setEditing] = useState(false);
  const names = useSettingsNames();
  const quantity = useQuantity();
  const money = useMoney();
  const seesCosts = useSeesCosts();

  if (error)
    return (
      <StatusMessage
        title={errorMessage(error, t, "materials.loadFailed")}
        onRetry={() => void refetch()}
      />
    );
  if (!material)
    return <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;

  const unit = names.unit(material.tracking_unit_id);
  return (
    <div className="flex flex-col gap-6">
      <ItemHeader
        back={{ to: "/stock/materials", label: t("hoard:stock.materials") }}
        title={material.name}
        marks={
          <>
            {isLow(material.on_hand, material.low_stock_limit) && <LowStockBadge />}
            {!material.inventoriable && (
              <Badge variant="outline">{t("materials.notInventoriable")}</Badge>
            )}
          </>
        }
        onEdit={() => setEditing(true)}
        remove={() => deleteMaterial(materialId)}
        onRemoved={() => {
          void queryClient.invalidateQueries({ queryKey: getListMaterialsQueryKey() });
          void navigate({ to: "/stock/materials" });
        }}
      />
      <ItemFacts
        facts={[
          [t("hoard:common.onHand"), `${quantity(material.on_hand)} ${unit}`],
          [
            t("hoard:common.lowStockLimit"),
            material.low_stock_limit && `${quantity(material.low_stock_limit)} ${unit}`,
          ],
          [t("hoard:common.sku"), material.sku],
          [t("hoard:common.category"), names.category(material.category_id)],
          [t("materials.trackingUnit"), unit],
          [
            t("materials.purchaseUnit"),
            material.purchase_unit_id !== null &&
              t("materials.purchaseUnitIs", {
                unit: names.unit(material.purchase_unit_id),
                conversion: quantity(material.purchase_conversion),
                trackingUnit: unit,
              }),
          ],
          [t("materials.supplierCode"), material.supplier_code],
          [
            t("items.averageCost"),
            seesCosts && material.average_cost && money(material.average_cost, { exact: true }),
          ],
        ]}
      />
      <ItemSection title={t("items.stock")}>
        <StockTable stock={material.stock} unit={unit} />
      </ItemSection>
      <ItemSection title={t("items.lots")}>
        <LotsTable lots={material.lots} />
      </ItemSection>
      <RecordStamps record={material} />
      <MaterialDialog material={material} open={editing} onOpenChange={setEditing} />
    </div>
  );
}
