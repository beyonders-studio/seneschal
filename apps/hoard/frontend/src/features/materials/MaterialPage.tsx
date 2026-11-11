import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  deleteMaterial,
  getListMaterialsQueryKey,
  useReadMaterial,
} from "@/api/generated/materials/materials";
import { LayoutsButton, RecordDetail } from "@/features/fields/records";
import { isLow } from "@/features/items/format";
import { ItemHeader } from "@/features/items/ItemHeader";
import { LowStockBadge } from "@/features/items/ItemList";
import { MATERIALS } from "@/features/materials/fields";
import { MaterialDialog } from "@/features/materials/MaterialDialog";
import { errorMessage } from "@/lib/errors";
import { Badge } from "@/ui/badge";
import { StatusMessage } from "@/ui/status-message";

/** One material, under Stock: its fields, properties, and stock and lots at each
 *  location, where its layout puts them. */
export function MaterialPage({ materialId }: { materialId: number }) {
  const { t } = useTranslation("catalog");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: material, error, refetch } = useReadMaterial(materialId);
  const [editing, setEditing] = useState(false);

  if (error)
    return (
      <StatusMessage
        title={errorMessage(error, t, "materials.loadFailed")}
        onRetry={() => void refetch()}
      />
    );
  if (!material)
    return <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;

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
        more={<LayoutsButton of={MATERIALS} rows={[material]} page={material} />}
      />
      <RecordDetail of={MATERIALS} item={material} onSaved={() => void refetch()} />
      <MaterialDialog material={material} open={editing} onOpenChange={setEditing} />
    </div>
  );
}
