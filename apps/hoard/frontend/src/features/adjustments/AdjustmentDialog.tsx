import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  getListAdjustmentsQueryKey,
  useCreateAdjustment,
  useUpdateAdjustment,
} from "@/api/generated/adjustments/adjustments";
import type { AdjustmentRead } from "@/api/generated/hoard.schemas";
import { toast } from "@/components/Toaster";
import { ItemField, LocationField } from "@/features/adjustments/fields";
import { today, useMovementLabels } from "@/features/adjustments/labels";
import { errorMessage } from "@/lib/errors";
import { useSeesCosts } from "@/lib/permissions";
import { Button } from "@/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/ui/dialog";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";
import { Textarea } from "@/ui/textarea";

/** The reason select's value for no reason. */
const NO_REASON = "none";

/**
 * Adds a change to stock by hand, or changes `adjustment`, a manual or
 * starting one. A starting adjustment's unit cost is typed in by a member who
 * sees costs. Closing it drops what was typed.
 */
export function AdjustmentDialog({
  open,
  onOpenChange,
  adjustment,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  adjustment?: AdjustmentRead;
}) {
  const { t } = useTranslation("movements");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {adjustment ? t("adjustments.editTitle") : t("adjustments.addTitle")}
          </DialogTitle>
        </DialogHeader>
        <AdjustmentForm adjustment={adjustment} onSaved={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function AdjustmentForm({
  adjustment,
  onSaved,
}: {
  adjustment?: AdjustmentRead;
  onSaved: () => void;
}) {
  const { t } = useTranslation("movements");
  const queries = useQueryClient();
  const seesCosts = useSeesCosts();
  const { options } = useMovementLabels();
  const [item, setItem] = useState(adjustment?.item.id ?? null);
  const [location, setLocation] = useState(adjustment?.location_id ?? null);
  const [datedOn, setDatedOn] = useState(adjustment?.dated_on ?? today());
  const [change, setChange] = useState(adjustment?.change ?? "");
  const [reason, setReason] = useState(adjustment?.reason_id ?? null);
  const [notes, setNotes] = useState(adjustment?.notes ?? "");
  const [unitCost, setUnitCost] = useState(adjustment?.unit_cost ?? "");
  const create = useCreateAdjustment();
  const update = useUpdateAdjustment();
  const costed = seesCosts && adjustment?.kind === "starting";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (item === null) return;
    const fields = {
      ...(location === null ? {} : { location_id: location }),
      dated_on: datedOn,
      change,
      reason_id: reason,
      notes: notes.trim() || null,
    };
    try {
      if (adjustment)
        await update.mutateAsync({
          adjustmentId: adjustment.id,
          data: { ...fields, ...(costed ? { unit_cost: unitCost || "0" } : {}) },
        });
      else await create.mutateAsync({ data: { item_id: item, ...fields } });
      void queries.invalidateQueries({ queryKey: getListAdjustmentsQueryKey() });
      onSaved();
    } catch (error) {
      toast.error(errorMessage(error, t, "adjustments.saveFailed"));
    }
  };

  const saving = create.isPending || update.isPending;
  return (
    <form onSubmit={submit} className="contents">
      <DialogBody className="space-y-4">
        {!adjustment && (
          <ItemField
            label={t("adjustments.item")}
            value={item}
            onChange={(picked) => setItem(picked.id)}
          />
        )}
        <div className="grid-cols-pair grid gap-4">
          <Field label={t("adjustments.change")} hint={t("adjustments.changeHint")}>
            <Input
              required
              inputMode="decimal"
              value={change}
              onChange={(event) => setChange(event.target.value)}
            />
          </Field>
          <Field label={t("hoard:common.date")}>
            <Input
              type="date"
              required
              value={datedOn}
              onChange={(event) => setDatedOn(event.target.value)}
            />
          </Field>
        </div>
        {costed && (
          <Field label={t("hoard:common.unitCost")}>
            <Input
              inputMode="decimal"
              value={unitCost}
              onChange={(event) => setUnitCost(event.target.value)}
            />
          </Field>
        )}
        <LocationField value={location} onChange={setLocation} />
        <Field label={t("adjustments.reason")}>
          <Select
            value={reason === null ? NO_REASON : String(reason)}
            onValueChange={(next) => setReason(next === NO_REASON ? null : Number(next))}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_REASON}>{t("hoard:common.none")}</SelectItem>
              {(options?.reasons ?? []).map((named) => (
                <SelectItem key={named.id} value={String(named.id)}>
                  {named.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label={t("hoard:common.notes")}>
          <Textarea value={notes} onChange={(event) => setNotes(event.target.value)} />
        </Field>
      </DialogBody>
      <DialogFooter>
        <Button type="submit" disabled={saving || item === null}>
          {saving ? t("hoard:common.saving") : t("hoard:common.save")}
        </Button>
      </DialogFooter>
    </form>
  );
}
