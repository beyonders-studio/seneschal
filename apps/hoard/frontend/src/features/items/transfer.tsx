import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  createTransfer,
  getListAdjustmentsQueryKey,
} from "@/api/generated/adjustments/adjustments";
import type { ItemLotRead, ItemStockRead } from "@/api/generated/hoard.schemas";
import { today } from "@/features/adjustments/labels";
import { useQuantity } from "@/features/items/format";
import { IdSelect } from "@/features/items/IdSelect";
import { useSettingsNames } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
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

/** An item whose stock may be moved: a material, a base or a variation,
 *  named where a product has several. */
export interface Movable {
  id: number;
  name?: string;
  stock: ItemStockRead[];
  lots: ItemLotRead[];
}

/** What a location holds of an item in no lot: its stock there less its lots'. */
const inNoLot = (item: Movable, locationId: number): number =>
  Number(item.stock.find((row) => row.location_id === locationId)?.quantity ?? 0) -
  item.lots
    .filter((lot) => lot.location_id === locationId)
    .reduce((held, lot) => held + Math.max(Number(lot.balance), 0), 0);

/**
 * Moving stock of one of `items` from one location to another, in a dialog:
 * out of one of its lots there, or out of what it holds in no lot, at its
 * average cost. A lot keeps its number, and a hold, at the other location.
 */
export function TransferDialog({
  items,
  unit,
  open,
  onOpenChange,
  onMoved,
}: {
  items: Movable[];
  unit?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMoved: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {open && (
          <TransferForm
            items={items}
            unit={unit}
            onMoved={() => {
              onOpenChange(false);
              onMoved();
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function TransferForm({
  items,
  unit,
  onMoved,
}: {
  items: Movable[];
  unit?: string;
  onMoved: () => void;
}) {
  const { t } = useTranslation("catalog");
  const queries = useQueryClient();
  const names = useSettingsNames();
  const quantity = useQuantity();
  const stocked = items.filter((item) => item.stock.some((row) => Number(row.quantity) > 0));
  const [itemId, setItemId] = useState<number | null>(stocked[0]?.id ?? null);
  const item = items.find((each) => each.id === itemId);
  const held = item?.stock.filter((row) => Number(row.quantity) > 0) ?? [];
  const [from, setFrom] = useState<number | null>(held[0]?.location_id ?? null);
  const lots = item?.lots.filter((lot) => lot.location_id === from && Number(lot.balance) > 0);
  const loose = item && from !== null ? inNoLot(item, from) : 0;
  const [lotId, setLotId] = useState<number | null>(lots?.[0]?.id ?? null);
  const [to, setTo] = useState<number | null>(null);
  const [amount, setAmount] = useState("");
  const [datedOn, setDatedOn] = useState(today());
  const lot = lots?.find((each) => each.id === lotId);
  const most = lot ? lot.balance : String(loose);

  const pickItem = (id: number | null) => {
    const next = items.find((each) => each.id === id);
    const at = next?.stock.find((row) => Number(row.quantity) > 0)?.location_id ?? null;
    setItemId(id);
    pickFrom(at, next);
  };
  const pickFrom = (locationId: number | null, of = item) => {
    setFrom(locationId);
    const first = of?.lots.find(
      (each) => each.location_id === locationId && Number(each.balance) > 0
    );
    setLotId(first?.id ?? null);
    if (to === locationId) setTo(null);
  };

  const move = useMutation({
    mutationFn: () =>
      createTransfer({
        item_id: itemId ?? 0,
        from_location_id: from ?? 0,
        to_location_id: to ?? 0,
        lot_id: lotId,
        quantity: amount,
        dated_on: datedOn,
      }),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: getListAdjustmentsQueryKey() });
      onMoved();
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (itemId !== null && from !== null && to !== null) move.mutate();
  };
  const withUnit = (value: string) => [quantity(value), unit].filter(Boolean).join(" ");

  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>{t("items.move.title")}</DialogTitle>
      </DialogHeader>
      <DialogBody className="flex flex-col gap-4">
        {items.length > 1 && (
          <Field label={t("products.variation")}>
            <IdSelect
              value={itemId}
              options={stocked.map((each) => ({ id: each.id, name: each.name ?? "" }))}
              onChange={pickItem}
            />
          </Field>
        )}
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("items.move.from")}>
            <IdSelect
              value={from}
              options={held.map((row) => ({
                id: row.location_id,
                name: names.location(row.location_id),
              }))}
              onChange={(id) => pickFrom(id)}
            />
          </Field>
          <Field label={t("items.move.to")}>
            <IdSelect
              value={to}
              options={names.locations.filter((place) => place.id !== from)}
              onChange={setTo}
            />
          </Field>
        </div>
        <Field label={t("items.lot")}>
          <IdSelect
            value={lotId}
            options={(lots ?? []).map((each) => ({
              id: each.id,
              name: t("items.move.lotLeft", { number: each.number, left: withUnit(each.balance) }),
            }))}
            onChange={setLotId}
            none={loose > 0 ? t("items.move.noLot", { left: withUnit(String(loose)) }) : undefined}
          />
        </Field>
        <div className="grid grid-cols-pair gap-4">
          <Field
            label={t("items.move.quantity")}
            hint={t("items.move.most", { most: withUnit(most) })}
          >
            <Input
              required
              inputMode="decimal"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
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
        <p className="text-sm text-muted-foreground">{t("items.move.hint")}</p>
        {move.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(move.error, t, "items.move.failed")}
          </p>
        )}
      </DialogBody>
      <DialogFooter
        submitLabel={t("items.move.submit")}
        disabled={itemId === null || from === null || to === null}
        pending={move.isPending}
        pendingLabel={t("hoard:common.saving")}
      />
    </form>
  );
}
