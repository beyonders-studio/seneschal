import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import type { OrderLineRead, OrderRead } from "@/api/generated/hoard.schemas";
import { useCreateOrderLine, useUpdateOrderLine } from "@/api/generated/orders/orders";
import { toast } from "@/components/Toaster";
import { ItemField } from "@/features/adjustments/fields";
import { errorMessage } from "@/lib/errors";
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

/** What the line dialog is for: selling something more, or changing a line. */
export type LineAction = { kind: "add" } | { kind: "edit"; line: OrderLineRead };

/**
 * The dialog `action` opens on a hand order's lines: a variation sold, how
 * many, and at what price, the variation's own when none is typed. Closing it
 * drops what was typed; `onSaved` takes the order as it is after.
 */
export function LineDialog({
  order,
  action,
  onClose,
  onSaved,
}: {
  order: OrderRead;
  action: LineAction | null;
  onClose: () => void;
  onSaved: (saved: OrderRead) => void;
}) {
  const { t } = useTranslation("orders");
  return (
    <Dialog open={action !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{action ? t(`lines.${action.kind}Title`) : null}</DialogTitle>
        </DialogHeader>
        {action && (
          <LineForm
            order={order}
            line={action.kind === "edit" ? action.line : undefined}
            onSaved={(saved) => {
              onClose();
              onSaved(saved);
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function LineForm({
  order,
  line,
  onSaved,
}: {
  order: OrderRead;
  line?: OrderLineRead;
  onSaved: (saved: OrderRead) => void;
}) {
  const { t } = useTranslation("orders");
  const [item, setItem] = useState(line?.item.id ?? null);
  const [quantity, setQuantity] = useState(line?.quantity ?? "");
  const [price, setPrice] = useState(line?.price ?? "");
  const create = useCreateOrderLine();
  const update = useUpdateOrderLine();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      if (line)
        onSaved(
          await update.mutateAsync({
            orderId: order.id,
            lineId: line.id,
            data: {
              ...(quantity !== line.quantity ? { quantity } : {}),
              ...(price && price !== line.price ? { price } : {}),
            },
          })
        );
      else if (item !== null)
        onSaved(
          await create.mutateAsync({
            orderId: order.id,
            data: { item_id: item, quantity, ...(price ? { price } : {}) },
          })
        );
    } catch (error) {
      toast.error(errorMessage(error, t, "lines.saveFailed"));
    }
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogBody className="space-y-4">
        {!line && (
          <ItemField
            kinds={["variation"]}
            label={t("lines.item")}
            value={item}
            onChange={setItem}
          />
        )}
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("hoard:common.quantity")}>
            <Input
              required
              inputMode="decimal"
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
            />
          </Field>
          <Field label={t("lines.price")} hint={line ? undefined : t("lines.priceHint")}>
            <Input
              inputMode="decimal"
              value={price}
              onChange={(event) => setPrice(event.target.value)}
            />
          </Field>
        </div>
      </DialogBody>
      <DialogFooter>
        <Button
          type="submit"
          disabled={create.isPending || update.isPending || (!line && item === null)}
        >
          {create.isPending || update.isPending ? t("hoard:common.saving") : t("hoard:common.save")}
        </Button>
      </DialogFooter>
    </form>
  );
}
