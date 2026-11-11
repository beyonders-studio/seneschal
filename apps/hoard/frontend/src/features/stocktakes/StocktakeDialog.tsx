import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { CategoryKind, type StocktakeRead } from "@/api/generated/hoard.schemas";
import {
  getListStocktakesQueryKey,
  getReadStocktakeQueryKey,
  useCreateStocktake,
} from "@/api/generated/stocktakes/stocktakes";
import { toast } from "@/components/Toaster";
import { LocationField } from "@/features/adjustments/fields";
import { today, useMovementLabels } from "@/features/adjustments/labels";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";
import { Textarea } from "@/ui/textarea";

/** The category select's value for every category. */
const ALL_CATEGORIES = "all";

/**
 * Starts a stocktake: of materials or products, at a location, of one category
 * or every one, counting each item or a random sample of them. Closing it
 * drops what was typed; `onStarted` takes the stocktake started.
 */
export function StocktakeDialog({
  open,
  onOpenChange,
  onStarted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onStarted: (started: StocktakeRead) => void;
}) {
  const { t } = useTranslation("movements");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("stocktakes.addTitle")}</DialogTitle>
        </DialogHeader>
        <StocktakeForm
          onStarted={(started) => {
            onOpenChange(false);
            onStarted(started);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

function StocktakeForm({ onStarted }: { onStarted: (started: StocktakeRead) => void }) {
  const { t } = useTranslation("movements");
  const queries = useQueryClient();
  const { options } = useMovementLabels();
  const [kind, setKind] = useState<CategoryKind>(CategoryKind.material);
  const [location, setLocation] = useState<number | null>(null);
  const [category, setCategory] = useState<number | null>(null);
  const [sampleSize, setSampleSize] = useState("");
  const [datedOn, setDatedOn] = useState(today());
  const [notes, setNotes] = useState("");
  const start = useCreateStocktake();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const started = await start.mutateAsync({
        data: {
          kind,
          ...(location === null ? {} : { location_id: location }),
          category_id: category,
          sample_size: sampleSize ? Number(sampleSize) : null,
          dated_on: datedOn,
          notes: notes.trim() || null,
        },
      });
      queries.setQueryData(getReadStocktakeQueryKey(started.id), started);
      void queries.invalidateQueries({ queryKey: getListStocktakesQueryKey() });
      onStarted(started);
    } catch (error) {
      toast.error(errorMessage(error, t, "stocktakes.startFailed"));
    }
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogBody className="space-y-4">
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("stocktakes.kind")}>
            <Select
              value={kind}
              onValueChange={(next) => {
                setKind(next as CategoryKind);
                setCategory(null);
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.values(CategoryKind).map((each) => (
                  <SelectItem key={each} value={each}>
                    {t(`stocktakes.kinds.${each}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label={t("hoard:common.category")}>
            <Select
              value={category === null ? ALL_CATEGORIES : String(category)}
              onValueChange={(next) => setCategory(next === ALL_CATEGORIES ? null : Number(next))}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_CATEGORIES}>{t("stocktakes.allCategories")}</SelectItem>
                {(options?.categories ?? [])
                  .filter((named) => named.kind === kind)
                  .map((named) => (
                    <SelectItem key={named.id} value={String(named.id)}>
                      {named.name}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </Field>
        </div>
        <LocationField value={location} onChange={setLocation} />
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("stocktakes.sampleSize")} hint={t("stocktakes.sampleSizeHint")}>
            <Input
              type="number"
              min={1}
              step={1}
              value={sampleSize}
              onChange={(event) => setSampleSize(event.target.value)}
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
        <Field label={t("hoard:common.notes")}>
          <Textarea value={notes} onChange={(event) => setNotes(event.target.value)} />
        </Field>
      </DialogBody>
      <DialogFooter>
        <Button type="submit" disabled={start.isPending}>
          {start.isPending ? t("hoard:common.saving") : t("stocktakes.start")}
        </Button>
      </DialogFooter>
    </form>
  );
}
