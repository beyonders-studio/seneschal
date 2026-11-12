import { useQueryClient } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import type { OrderRead } from "@/api/generated/hoard.schemas";
import { usePlaceOrderHolds } from "@/api/generated/holds/holds";
import { getReadOrderQueryKey } from "@/api/generated/orders/orders";
import { toast } from "@/components/Toaster";
import { useHoldReasonOptions } from "@/features/holds/reasons";
import { IdSelect } from "@/features/items/IdSelect";
import { errorMessage } from "@/lib/errors";
import { useSession } from "@/session";
import { Button } from "@/ui/button";
import { Checkbox } from "@/ui/checkbox";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/ui/dialog";
import { Field } from "@/ui/field";
import { Label } from "@/ui/label";
import { Textarea } from "@/ui/textarea";

/** Puts the lots an order drew, recorded or assumed, on hold as suspect,
 *  when a problem with the order is reported. */
export function HoldFromOrder({ order }: { order: OrderRead }) {
  const { t } = useTranslation("holds");
  const { read_only } = useSession();
  const [open, setOpen] = useState(false);
  const drew = order.shipments.some((shipment) => shipment.lines.some((line) => line.lot));
  if (!drew) return null;
  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)} disabled={read_only}>
        <ShieldAlert aria-hidden />
        {t("order.button")}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          {open && <OrderHoldForm order={order} onDone={() => setOpen(false)} />}
        </DialogContent>
      </Dialog>
    </>
  );
}

function OrderHoldForm({ order, onDone }: { order: OrderRead; onDone: () => void }) {
  const { t } = useTranslation("holds");
  const queries = useQueryClient();
  const { reasons, options } = useHoldReasonOptions();
  const suspect = reasons.find((reason) => reason.key === "suspect")?.id ?? null;
  const [chosen, setChosen] = useState<number | null>(null);
  const [notes, setNotes] = useState("");
  const [madeFrom, setMadeFrom] = useState(false);
  const place = usePlaceOrderHolds();
  const reason = chosen ?? suspect;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const placed = await place.mutateAsync({
        orderId: order.id,
        data: { reason_id: reason, notes: notes.trim() || null, made_from: madeFrom },
      });
      toast.success(
        placed.length ? t("place.placed", { count: placed.length }) : t("order.nothing")
      );
      void queries.invalidateQueries({ queryKey: getReadOrderQueryKey(order.id) });
      onDone();
    } catch (error) {
      toast.error(errorMessage(error, t, "order.failed"));
    }
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>{t("order.title", { code: order.code })}</DialogTitle>
        <DialogDescription>{t("order.body")}</DialogDescription>
      </DialogHeader>
      <DialogBody className="space-y-4">
        <Field label={t("order.reason")}>
          <IdSelect value={reason} options={options} onChange={setChosen} />
        </Field>
        <Field label={t("hoard:common.notes")}>
          <Textarea value={notes} onChange={(event) => setNotes(event.target.value)} />
        </Field>
        <div className="flex items-center gap-2">
          <Checkbox
            id="order-hold-made-from"
            checked={madeFrom}
            onCheckedChange={(checked) => setMadeFrom(checked === true)}
          />
          <Label htmlFor="order-hold-made-from">{t("order.madeFrom")}</Label>
        </div>
      </DialogBody>
      <DialogFooter
        submitLabel={t("order.submit")}
        pending={place.isPending}
        pendingLabel={t("hoard:common.saving")}
      />
    </form>
  );
}
