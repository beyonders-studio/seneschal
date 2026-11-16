import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  createBase,
  getListBasesQueryKey,
  getReadBaseQueryKey,
  updateBase,
} from "@/api/generated/bases/bases";
import { type BaseCreate, type BaseDetail, ItemStatus } from "@/api/generated/hoard.schemas";
import { CureDaysField, days } from "@/features/items/CureDaysField";
import { orNull } from "@/features/items/format";
import { IdSelect } from "@/features/items/IdSelect";
import { StatusField } from "@/features/items/ItemList";
import { withPicture } from "@/features/items/ItemPicture";
import { NO_START, StartFields, startBody } from "@/features/items/start";
import { UnitPicker } from "@/features/items/UnitPicker";
import { useSettingsNames } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
import { useSeesCosts } from "@/lib/permissions";
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
import { Textarea } from "@/ui/textarea";

/**
 * Adding a base, with its picture, bin and starting stock, or editing `base`,
 * in a dialog. Closing it drops what was typed; saving answers the base as
 * Hoard keeps it.
 */
export function BaseDialog({
  base,
  open,
  onOpenChange,
  onSaved,
}: {
  base?: BaseDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved?: (base: BaseDetail) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {open && (
          <BaseForm
            base={base}
            onSaved={(saved) => {
              onOpenChange(false);
              onSaved?.(saved);
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function BaseForm({ base, onSaved }: { base?: BaseDetail; onSaved: (base: BaseDetail) => void }) {
  const { t } = useTranslation("catalog");
  const queryClient = useQueryClient();
  const seesCosts = useSeesCosts();
  const { categoriesOf, unit } = useSettingsNames();
  const [name, setName] = useState(base?.name ?? "");
  const [status, setStatus] = useState<ItemStatus>(base?.status ?? ItemStatus.active);
  const [notes, setNotes] = useState(base?.notes ?? "");
  const [start, setStart] = useState(NO_START);
  const [sku, setSku] = useState(base?.sku ?? "");
  const [categoryId, setCategoryId] = useState(base?.category_id ?? null);
  const [trackingUnitId, setTrackingUnitId] = useState(base?.tracking_unit_id ?? null);
  const [lowStockLimit, setLowStockLimit] = useState(base?.low_stock_limit ?? "");
  const [cureDays, setCureDays] = useState(base?.cure_days?.toString() ?? "");
  const save = useMutation({
    mutationFn: async (body: BaseCreate) => {
      if (base) return updateBase(base.id, body);
      const made = await createBase(body);
      return start.picture ? withPicture(made, start.picture, t) : made;
    },
    onSuccess: async (saved) => {
      queryClient.setQueryData(getReadBaseQueryKey(saved.id), saved);
      await queryClient.invalidateQueries({ queryKey: getListBasesQueryKey() });
      onSaved(saved);
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (trackingUnitId === null) return;
    save.mutate({
      name: name.trim(),
      status,
      notes: orNull(notes),
      sku: orNull(sku),
      category_id: categoryId,
      tracking_unit_id: trackingUnitId,
      low_stock_limit: orNull(lowStockLimit),
      cure_days: days(cureDays),
      ...(base ? {} : { start: startBody(start, true, seesCosts) }),
    });
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>{base ? t("bases.edit") : t("bases.add")}</DialogTitle>
      </DialogHeader>
      <DialogBody className="flex flex-col gap-4">
        <Field label={t("hoard:common.name")}>
          <Input value={name} onChange={(event) => setName(event.target.value)} required />
        </Field>
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("hoard:common.sku")}>
            <Input value={sku} onChange={(event) => setSku(event.target.value)} />
          </Field>
          <Field label={t("hoard:common.category")}>
            <IdSelect
              value={categoryId}
              options={categoriesOf("base")}
              onChange={setCategoryId}
              none={t("hoard:common.none")}
            />
          </Field>
          <Field label={t("materials.trackingUnit")}>
            <UnitPicker value={trackingUnitId} onChange={setTrackingUnitId} />
          </Field>
          <Field label={t("hoard:common.lowStockLimit")}>
            <Input
              inputMode="decimal"
              value={lowStockLimit}
              onChange={(event) => setLowStockLimit(event.target.value)}
            />
          </Field>
          <CureDaysField value={cureDays} onChange={setCureDays} />
          {base && <StatusField value={status} onChange={setStatus} />}
        </div>
        <Field label={t("hoard:common.notes")}>
          <Textarea value={notes} onChange={(event) => setNotes(event.target.value)} />
        </Field>
        {!base && <StartFields value={start} onChange={setStart} unit={unit(trackingUnitId)} />}
        {save.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(save.error, t, "bases.saveFailed")}
          </p>
        )}
      </DialogBody>
      <DialogFooter
        submitLabel={t("hoard:common.save")}
        pending={save.isPending}
        pendingLabel={t("hoard:common.saving")}
        disabled={!name.trim() || trackingUnitId === null}
      />
    </form>
  );
}
