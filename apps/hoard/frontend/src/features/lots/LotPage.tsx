import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { LotRead } from "@/api/generated/hoard.schemas";
import { useReadLot } from "@/api/generated/lots/lots";
import { useMovementLabels } from "@/features/adjustments/labels";
import { LayoutsButton, RecordDetail } from "@/features/fields/records";
import { LOTS, OnHoldBadge } from "@/features/items/lots";
import { errorMessage } from "@/lib/errors";
import { StatusMessage } from "@/ui/status-message";

/** The page a lot is of: its item's, a variation's being its product's. */
function ItemLink({ lot }: { lot: LotRead }) {
  const { item } = lot;
  const label = useMovementLabels().item(item);
  const className = "inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline";
  const content = (
    <>
      <ArrowLeft aria-hidden className="size-4" />
      {label}
    </>
  );
  if (item.kind === "material")
    return (
      <Link
        to="/stock/materials/$materialId"
        params={{ materialId: String(item.id) }}
        className={className}
      >
        {content}
      </Link>
    );
  if (item.kind === "base")
    return (
      <Link to="/stock/bases/$baseId" params={{ baseId: String(item.id) }} className={className}>
        {content}
      </Link>
    );
  return (
    <Link
      to="/stock/products/$productId"
      params={{ productId: String(lot.product_id) }}
      className={className}
    >
      {content}
    </Link>
  );
}

/** One lot, under Stock: its fields and properties, its hold, its trace, its
 *  declarations and its documents, where its layout puts them. */
export function LotPage({ lotId }: { lotId: number }) {
  const { t } = useTranslation("fields");
  const { data: lot, error, refetch } = useReadLot(lotId);

  if (error)
    return (
      <StatusMessage
        title={errorMessage(error, t, "hoard:errors.NOT_FOUND")}
        onRetry={() => void refetch()}
      />
    );
  if (!lot) return <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <ItemLink lot={lot} />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold tracking-tight">
              {t("lot", { number: lot.number })}
            </h2>
            {lot.on_hold && <OnHoldBadge />}
          </div>
          <LayoutsButton of={LOTS} rows={[lot]} page={lot} />
        </div>
      </div>
      <RecordDetail of={LOTS} item={lot} onSaved={() => void refetch()} />
    </div>
  );
}
