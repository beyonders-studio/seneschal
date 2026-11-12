import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import type { LotRead } from "@/api/generated/hoard.schemas";
import { useDiscardHold, usePlaceHold, useReleaseHold } from "@/api/generated/holds/holds";
import { getReadLotQueryKey } from "@/api/generated/lots/lots";
import { useListSettingsReasons } from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { today, useMovementLabels } from "@/features/adjustments/labels";
import { useHoldReasonOptions } from "@/features/holds/reasons";
import { IdSelect } from "@/features/items/IdSelect";
import { useReasonName } from "@/features/settings/names";
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
import { Input } from "@/ui/input";
import { Label } from "@/ui/label";
import { Textarea } from "@/ui/textarea";

type Action = "place" | "release" | "discard";

const linkClass = "underline-offset-4 hover:underline";

/** A lot's hold, on its page: its reason and ready date, and putting it on
 *  hold, releasing it or discarding it. */
export function HoldSection({ lot }: { lot: LotRead }) {
  const { t } = useTranslation("holds");
  const { read_only } = useSession();
  const { date } = useMovementLabels();
  const { reasons, name } = useHoldReasonOptions();
  const [action, setAction] = useState<Action | null>(null);
  const { hold } = lot;
  const reason = reasons.find((each) => each.id === hold?.reason_id);

  return (
    <div className="flex flex-col items-start gap-2 text-sm">
      {hold ? (
        <>
          <p>{reason ? t("section.reasonIs", { reason: name(reason) }) : t("section.onHold")}</p>
          {hold.ready_on && (
            <p className="text-muted-foreground">
              {hold.ready_on <= today()
                ? t("section.ready", { date: date(hold.ready_on) })
                : t("section.readyOn", { date: date(hold.ready_on) })}
            </p>
          )}
          {hold.notes && <p className="whitespace-pre-line">{hold.notes}</p>}
          {hold.run_id !== null && (
            <Link
              to="/production/runs/$runId"
              params={{ runId: String(hold.run_id) }}
              className={linkClass}
            >
              {t("section.fromRun")}
            </Link>
          )}
          {hold.order_id !== null && (
            <Link
              to="/orders/$orderId"
              params={{ orderId: String(hold.order_id) }}
              className={linkClass}
            >
              {t("section.fromOrder")}
            </Link>
          )}
          {hold.from_hold_id !== null && (
            <p className="text-muted-foreground">{t("section.fromHold")}</p>
          )}
          {!read_only && (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => setAction("release")}>
                {t("section.release")}
              </Button>
              <Button size="sm" variant="outline" onClick={() => setAction("discard")}>
                {t("section.discard")}
              </Button>
            </div>
          )}
        </>
      ) : (
        <>
          <p className="text-muted-foreground">{t("section.available")}</p>
          {!read_only && (
            <Button size="sm" variant="outline" onClick={() => setAction("place")}>
              {t("section.place")}
            </Button>
          )}
        </>
      )}
      <Dialog open={action !== null} onOpenChange={(open) => !open && setAction(null)}>
        <DialogContent>
          {action === "place" && <PlaceForm lot={lot} onDone={() => setAction(null)} />}
          {action === "release" && <ReleaseForm lot={lot} onDone={() => setAction(null)} />}
          {action === "discard" && <DiscardForm lot={lot} onDone={() => setAction(null)} />}
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** Refreshes the lot once its hold changed. */
function useChanged(lot: LotRead, onDone: () => void) {
  const queries = useQueryClient();
  return () => {
    void queries.invalidateQueries({ queryKey: getReadLotQueryKey(lot.id) });
    onDone();
  };
}

function PlaceForm({ lot, onDone }: { lot: LotRead; onDone: () => void }) {
  const { t } = useTranslation("holds");
  const { options } = useHoldReasonOptions();
  const [reason, setReason] = useState<number | null>(null);
  const [readyOn, setReadyOn] = useState("");
  const [notes, setNotes] = useState("");
  const [madeFrom, setMadeFrom] = useState(false);
  const place = usePlaceHold();
  const changed = useChanged(lot, onDone);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (reason === null) return;
    try {
      const placed = await place.mutateAsync({
        data: {
          lot_id: lot.id,
          reason_id: reason,
          ready_on: readyOn || null,
          notes: notes.trim() || null,
          made_from: madeFrom,
        },
      });
      toast.success(t("place.placed", { count: placed.length }));
      changed();
    } catch (error) {
      toast.error(errorMessage(error, t, "place.failed"));
    }
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>{t("place.title", { number: lot.number })}</DialogTitle>
      </DialogHeader>
      <DialogBody className="space-y-4">
        <Field label={t("place.reason")}>
          <IdSelect value={reason} options={options} onChange={setReason} />
        </Field>
        <Field label={t("place.readyOn")} hint={t("place.readyOnHint")}>
          <Input type="date" value={readyOn} onChange={(event) => setReadyOn(event.target.value)} />
        </Field>
        <Field label={t("hoard:common.notes")}>
          <Textarea value={notes} onChange={(event) => setNotes(event.target.value)} />
        </Field>
        <div className="flex items-center gap-2">
          <Checkbox
            id="hold-made-from"
            checked={madeFrom}
            onCheckedChange={(checked) => setMadeFrom(checked === true)}
          />
          <Label htmlFor="hold-made-from">{t("place.madeFrom")}</Label>
        </div>
      </DialogBody>
      <DialogFooter
        submitLabel={t("place.submit")}
        pending={place.isPending}
        pendingLabel={t("hoard:common.saving")}
        disabled={reason === null}
      />
    </form>
  );
}

function ReleaseForm({ lot, onDone }: { lot: LotRead; onDone: () => void }) {
  const { t } = useTranslation("holds");
  const [endedOn, setEndedOn] = useState(today());
  const release = useReleaseHold();
  const changed = useChanged(lot, onDone);
  const hold = lot.hold;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!hold) return;
    try {
      await release.mutateAsync({ holdId: hold.id, data: { ended_on: endedOn } });
      changed();
    } catch (error) {
      toast.error(errorMessage(error, t, "release.failed"));
    }
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>{t("release.title", { number: lot.number })}</DialogTitle>
        <DialogDescription>{t("release.body")}</DialogDescription>
      </DialogHeader>
      <DialogBody>
        <Field label={t("release.endedOn")}>
          <Input
            type="date"
            required
            value={endedOn}
            onChange={(event) => setEndedOn(event.target.value)}
          />
        </Field>
      </DialogBody>
      <DialogFooter
        submitLabel={t("release.submit")}
        pending={release.isPending}
        pendingLabel={t("hoard:common.saving")}
      />
    </form>
  );
}

function DiscardForm({ lot, onDone }: { lot: LotRead; onDone: () => void }) {
  const { t } = useTranslation("holds");
  const reasons = useListSettingsReasons().data ?? [];
  const reasonName = useReasonName();
  const [reason, setReason] = useState<number | null>(null);
  const [quantity, setQuantity] = useState("");
  const [endedOn, setEndedOn] = useState(today());
  const discard = useDiscardHold();
  const changed = useChanged(lot, onDone);
  const hold = lot.hold;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!hold || reason === null) return;
    try {
      await discard.mutateAsync({
        holdId: hold.id,
        data: { reason_id: reason, quantity: quantity.trim() || null, ended_on: endedOn },
      });
      changed();
    } catch (error) {
      toast.error(errorMessage(error, t, "discard.failed"));
    }
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>{t("discard.title", { number: lot.number })}</DialogTitle>
      </DialogHeader>
      <DialogBody className="space-y-4">
        <Field label={t("discard.reason")}>
          <IdSelect
            value={reason}
            options={reasons.map((named) => ({ id: named.id, name: reasonName(named) }))}
            onChange={setReason}
          />
        </Field>
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("discard.quantity")} hint={t("discard.quantityHint")}>
            <Input
              inputMode="decimal"
              value={quantity}
              placeholder={lot.balance}
              onChange={(event) => setQuantity(event.target.value)}
            />
          </Field>
          <Field label={t("discard.endedOn")}>
            <Input
              type="date"
              required
              value={endedOn}
              onChange={(event) => setEndedOn(event.target.value)}
            />
          </Field>
        </div>
      </DialogBody>
      <DialogFooter
        submitLabel={t("discard.submit")}
        pending={discard.isPending}
        pendingLabel={t("hoard:common.saving")}
        disabled={reason === null}
      />
    </form>
  );
}
