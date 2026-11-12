import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { deleteBase, getListBasesQueryKey, useReadBase } from "@/api/generated/bases/bases";
import { BaseDialog } from "@/features/bases/BaseDialog";
import { BASES } from "@/features/bases/fields";
import { LayoutsButton, RecordDetail } from "@/features/fields/records";
import { isLow } from "@/features/items/format";
import { ItemHeader } from "@/features/items/ItemHeader";
import { LowStockBadge } from "@/features/items/ItemList";
import { errorMessage } from "@/lib/errors";
import { StatusMessage } from "@/ui/status-message";

/** One base, under Stock: its fields, properties, recipe, and stock and lots at
 *  each location, where its layout puts them. */
export function BasePage({ baseId }: { baseId: number }) {
  const { t } = useTranslation("catalog");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: base, error, refetch } = useReadBase(baseId);
  const [editing, setEditing] = useState(false);

  if (error)
    return (
      <StatusMessage
        title={errorMessage(error, t, "bases.loadFailed")}
        onRetry={() => void refetch()}
      />
    );
  if (!base) return <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;

  return (
    <div className="flex flex-col gap-6">
      <ItemHeader
        back={{ to: "/stock/bases", label: t("hoard:stock.bases") }}
        title={base.name}
        marks={isLow(base.on_hand, base.low_stock_limit) && <LowStockBadge />}
        onEdit={() => setEditing(true)}
        remove={() => deleteBase(baseId)}
        onRemoved={() => {
          void queryClient.invalidateQueries({ queryKey: getListBasesQueryKey() });
          void navigate({ to: "/stock/bases" });
        }}
        more={<LayoutsButton of={BASES} rows={[base]} page={base} />}
      />
      <RecordDetail of={BASES} item={base} onSaved={() => void refetch()} />
      <BaseDialog base={base} open={editing} onOpenChange={setEditing} />
    </div>
  );
}
