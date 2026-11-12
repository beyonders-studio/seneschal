import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import type { TraceLotRef, TraceStepRead } from "@/api/generated/hoard.schemas";
import { useMovementLabels } from "@/features/adjustments/labels";
import { useQuantity } from "@/features/items/format";
import { Badge } from "@/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

const LINK = "underline-offset-4 hover:underline";

/** The run, order or purchase a step is, opening its page. */
function StepRecord({ step }: { step: TraceStepRead }) {
  const { t } = useTranslation("trace");
  const label = t(`steps.kinds.${step.kind}`, { code: step.code });
  if (step.kind === "run")
    return (
      <Link
        to="/production/runs/$runId"
        params={{ runId: String(step.record_id) }}
        className={LINK}
      >
        {label}
      </Link>
    );
  if (step.kind === "purchase")
    return (
      <Link
        to="/stock/purchases/$purchaseId"
        params={{ purchaseId: String(step.record_id) }}
        className={LINK}
      >
        {label}
      </Link>
    );
  if (step.order_id === null) return label;
  return (
    <Link to="/orders/$orderId" params={{ orderId: String(step.order_id) }} className={LINK}>
      {label}
    </Link>
  );
}

/** A lot a trace names, opening its page. */
export function LotLink({ lot }: { lot: TraceLotRef | null }) {
  if (!lot) return null;
  return (
    <Link to="/stock/lots/$lotId" params={{ lotId: String(lot.id) }} className={LINK}>
      {lot.number}
    </Link>
  );
}

/** Who else a step names: a shipment's customer, or a consumer, or a
 *  purchase's supplier. */
export function useStepParty(): (step: TraceStepRead) => string {
  const { t } = useTranslation("trace");
  return (step) => {
    if (step.party) return step.party.name ?? t("steps.unnamedContact");
    return step.kind === "shipment" ? t("steps.consumer") : "";
  };
}

/** Marks a step that only may have happened. */
export function MayHaveBadge() {
  const { t } = useTranslation("trace");
  return <Badge variant="outline">{t("steps.mayHave")}</Badge>;
}

/** Steps of a trace, one a row: when, which record, what moved and from
 *  which lot, how much, and with whom. */
export function StepsTable({ steps, empty }: { steps: TraceStepRead[]; empty?: string }) {
  const { t } = useTranslation("trace");
  const labels = useMovementLabels();
  const quantity = useQuantity();
  const party = useStepParty();
  if (steps.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("hoard:common.date")}</TableHead>
          <TableHead>{t("steps.record")}</TableHead>
          <TableHead>{t("steps.item")}</TableHead>
          <TableHead>{t("steps.lot")}</TableHead>
          <TableHead className="text-end">{t("steps.quantity")}</TableHead>
          <TableHead>{t("steps.party")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {steps.map((step) => (
          <TableRow
            key={`${step.kind}-${step.record_id}-${step.from_lot?.id}-${step.lot?.id}-${step.item.id}-${step.quantity}-${step.level}`}
          >
            <TableCell>{labels.date(step.dated_on)}</TableCell>
            <TableCell>
              <span className="inline-flex flex-wrap items-center gap-2">
                <StepRecord step={step} />
                {step.assumed && <MayHaveBadge />}
              </span>
            </TableCell>
            <TableCell>{labels.item(step.item)}</TableCell>
            <TableCell>
              <LotLink lot={step.lot ?? step.from_lot} />
            </TableCell>
            <TableCell className="text-end tabular-nums">{quantity(step.quantity)}</TableCell>
            <TableCell>{party(step)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
