import { type FormEvent, type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import type { PurchaseLineRead, PurchaseRead } from "@/api/generated/hoard.schemas";
import {
  useCreatePurchaseLine,
  useReceivePurchaseLine,
  useUpdatePurchaseLine,
} from "@/api/generated/purchases/purchases";
import { toast } from "@/components/Toaster";
import { ItemField } from "@/features/adjustments/fields";
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
import { Textarea } from "@/ui/textarea";

/** What a dialog on a purchase's line is for: adding one, changing one, or receiving one. */
export type LineAction =
  | { kind: "add" }
  | { kind: "edit"; line: PurchaseLineRead }
  | { kind: "receive"; line: PurchaseLineRead };

/**
 * The dialog `action` opens on `purchase`'s lines. Closing it drops what was
 * typed; `onSaved` takes the purchase as it is after.
 */
export function LineDialog({
  purchase,
  action,
  onClose,
  onSaved,
}: {
  purchase: PurchaseRead;
  action: LineAction | null;
  onClose: () => void;
  onSaved: (saved: PurchaseRead) => void;
}) {
  const { t } = useTranslation("movements");
  const done = (saved: PurchaseRead) => {
    onClose();
    onSaved(saved);
  };
  return (
    <Dialog open={action !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{action ? t(`purchases.lines.${action.kind}Title`) : null}</DialogTitle>
        </DialogHeader>
        {action?.kind === "receive" ? (
          <ReceiveForm purchase={purchase} line={action.line} onSaved={done} />
        ) : action ? (
          <LineForm
            purchase={purchase}
            line={action.kind === "edit" ? action.line : undefined}
            onSaved={done}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function DialogForm({
  onSubmit,
  saving,
  children,
  submitLabel,
}: {
  onSubmit: () => Promise<void>;
  saving: boolean;
  children: ReactNode;
  submitLabel?: string;
}) {
  const { t } = useTranslation("movements");
  return (
    <form
      className="contents"
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        void onSubmit();
      }}
    >
      <DialogBody className="space-y-4">{children}</DialogBody>
      <DialogFooter>
        <Button type="submit" disabled={saving}>
          {saving ? t("hoard:common.saving") : (submitLabel ?? t("hoard:common.save"))}
        </Button>
      </DialogFooter>
    </form>
  );
}

function LineForm({
  purchase,
  line,
  onSaved,
}: {
  purchase: PurchaseRead;
  line?: PurchaseLineRead;
  onSaved: (saved: PurchaseRead) => void;
}) {
  const { t } = useTranslation("movements");
  const seesCosts = useSeesCosts();
  const { item: itemName } = useMovementLabels();
  const [itemId, setItemId] = useState(line?.item.id ?? null);
  const [quantity, setQuantity] = useState(line?.purchase_quantity ?? "");
  const [tracking, setTracking] = useState(line?.tracking_quantity ?? "");
  const [unitCost, setUnitCost] = useState(line?.unit_cost ?? "");
  const [receivedOn, setReceivedOn] = useState(line?.received_on ?? "");
  const create = useCreatePurchaseLine();
  const update = useUpdatePurchaseLine();

  const submit = async () => {
    if (itemId === null) return;
    // A tracking quantity left as it was is made again from a new purchase quantity.
    const amounts = {
      ...(tracking && tracking !== line?.tracking_quantity ? { tracking_quantity: tracking } : {}),
      ...(seesCosts ? { unit_cost: unitCost || "0" } : {}),
    };
    try {
      onSaved(
        line
          ? await update.mutateAsync({
              purchaseId: purchase.id,
              lineId: line.id,
              data: {
                ...(itemId !== line.item.id ? { item_id: itemId } : {}),
                ...(quantity !== line.purchase_quantity ? { purchase_quantity: quantity } : {}),
                ...(receivedOn !== (line.received_on ?? "") ? { received_on: receivedOn } : {}),
                ...amounts,
              },
            })
          : await create.mutateAsync({
              purchaseId: purchase.id,
              data: { item_id: itemId, purchase_quantity: quantity, ...amounts },
            })
      );
    } catch (error) {
      toast.error(errorMessage(error, t, "purchases.saveFailed"));
    }
  };

  return (
    <DialogForm onSubmit={submit} saving={create.isPending || update.isPending}>
      <ItemField
        bought
        label={t("purchases.lines.item")}
        value={itemId}
        chosen={line ? itemName(line.item) : null}
        onChange={setItemId}
      />
      <div className="grid grid-cols-pair gap-4">
        <Field label={t("purchases.lines.purchaseQuantity")}>
          <Input
            required
            inputMode="decimal"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
          />
        </Field>
        <Field
          label={t("purchases.lines.trackingQuantity")}
          hint={t("purchases.lines.trackingHint")}
        >
          <Input
            inputMode="decimal"
            value={tracking}
            onChange={(event) => setTracking(event.target.value)}
          />
        </Field>
      </div>
      {seesCosts && (
        <Field label={t("hoard:common.unitCost")} hint={t("purchases.lines.unitCostHint")}>
          <Input
            inputMode="decimal"
            value={unitCost}
            onChange={(event) => setUnitCost(event.target.value)}
          />
        </Field>
      )}
      {line?.received_on && (
        <ReceivedOnField purchase={purchase} value={receivedOn} onChange={setReceivedOn} />
      )}
    </DialogForm>
  );
}

function ReceiveForm({
  purchase,
  line,
  onSaved,
}: {
  purchase: PurchaseRead;
  line: PurchaseLineRead;
  onSaved: (saved: PurchaseRead) => void;
}) {
  const { t } = useTranslation("movements");
  const { quantity: amount } = useMovementLabels();
  const [receivedOn, setReceivedOn] = useState(today());
  const [quantity, setQuantity] = useState(line.tracking_quantity);
  const [number, setNumber] = useState("");
  const [expiresOn, setExpiresOn] = useState("");
  const [notes, setNotes] = useState("");
  const receive = useReceivePurchaseLine();

  const submit = async () => {
    try {
      onSaved(
        await receive.mutateAsync({
          purchaseId: purchase.id,
          lineId: line.id,
          data: {
            received_on: receivedOn,
            // Less than the whole line receives that much, the rest staying on order.
            ...(quantity !== line.tracking_quantity ? { quantity } : {}),
            ...(number.trim() ? { number: number.trim() } : {}),
            ...(expiresOn ? { expires_on: expiresOn } : {}),
            ...(notes.trim() ? { notes: notes.trim() } : {}),
          },
        })
      );
    } catch (error) {
      toast.error(errorMessage(error, t, "purchases.receiveFailed"));
    }
  };

  return (
    <DialogForm
      onSubmit={submit}
      saving={receive.isPending}
      submitLabel={t("purchases.lines.receive")}
    >
      <div className="grid grid-cols-pair gap-4">
        <ReceivedOnField purchase={purchase} value={receivedOn} onChange={setReceivedOn} />
        <Field
          label={t("purchases.lines.quantityReceived")}
          hint={t("purchases.lines.quantityReceivedHint", {
            quantity: amount(line.tracking_quantity),
          })}
        >
          <Input
            required
            inputMode="decimal"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
          />
        </Field>
      </div>
      <Field label={t("purchases.lines.lotNumber")} hint={t("purchases.lines.lotNumberHint")}>
        <Input value={number} onChange={(event) => setNumber(event.target.value)} />
      </Field>
      <Field label={t("purchases.lines.expiresOn")}>
        <Input
          type="date"
          value={expiresOn}
          onChange={(event) => setExpiresOn(event.target.value)}
        />
      </Field>
      <Field label={t("hoard:common.notes")}>
        <Textarea value={notes} onChange={(event) => setNotes(event.target.value)} />
      </Field>
    </DialogForm>
  );
}

/** The day a line arrived: never before its purchase's date. */
function ReceivedOnField({
  purchase,
  value,
  onChange,
}: {
  purchase: PurchaseRead;
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation("movements");
  return (
    <Field label={t("purchases.lines.receivedOn")}>
      <Input
        type="date"
        required
        min={purchase.dated_on}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  );
}
