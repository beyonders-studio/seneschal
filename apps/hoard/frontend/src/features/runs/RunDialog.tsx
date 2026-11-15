import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import type { RunPlan, RunPreview, RunRead } from "@/api/generated/hoard.schemas";
import { listMemberNames, searchMemberNames } from "@/api/generated/members/members";
import {
  getListRunsQueryKey,
  getReadRunQueryKey,
  previewRun,
  useCreateRun,
  useUpdateRun,
} from "@/api/generated/runs/runs";
import { toast } from "@/components/Toaster";
import { ItemField, LocationField } from "@/features/adjustments/fields";
import { useMovementLabels } from "@/features/adjustments/labels";
import { useQuantity } from "@/features/items/format";
import { IdSelect } from "@/features/items/IdSelect";
import { UnitPicker } from "@/features/items/UnitPicker";
import { useSettingsNames } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";
import { Checkbox } from "@/ui/checkbox";
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
import { Label } from "@/ui/label";
import { useDebouncedValue } from "@/ui/lib/useDebouncedValue";
import { type Member, MemberPicker, type SearchMembers } from "@/ui/member-picker";
import { Switch } from "@/ui/switch";

/** Whether `text` is a number above zero, as the API takes one. */
const isPositive = (text: string) => text.trim() !== "" && Number(text) > 0;

/**
 * Opens a production run, or changes `run`: what it makes (only when opening
 * it), its code, planned batches and location (until it is completed), its
 * deadline, expiry, assignee and labour. Answers the run saved.
 */
export function RunDialog({
  open,
  onOpenChange,
  run,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  run?: RunRead;
  onSaved?: (saved: RunRead) => void;
}) {
  const { t } = useTranslation("production");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{run ? t("runs.editTitle") : t("runs.addTitle")}</DialogTitle>
        </DialogHeader>
        <RunForm
          run={run}
          onSaved={(saved) => {
            onOpenChange(false);
            onSaved?.(saved);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

/** Finds the community's members by name, known by the references Hoard keeps. */
function useSearchMembers(): SearchMembers<string, string> {
  const { t } = useTranslation("connection");
  return useCallback<SearchMembers<string, string>>(
    async (_scope, query) => {
      const named =
        "search" in query
          ? await searchMemberNames({ search: query.search })
          : query.ids.length
            ? await listMemberNames({ member_ref: query.ids })
            : [];
      return named.map(({ member_ref, name }): Member<string> => {
        const shown = name ?? t("members.former");
        return { id: member_ref, name: shown, initials: shown.slice(0, 2).toUpperCase() };
      });
    },
    [t]
  );
}

function RunForm({ run, onSaved }: { run?: RunRead; onSaved: (saved: RunRead) => void }) {
  const { t } = useTranslation("production");
  const queries = useQueryClient();
  const searchMembers = useSearchMembers();
  const [item, setItem] = useState<number | null>(null);
  const [code, setCode] = useState(run?.code ?? "");
  const [batches, setBatches] = useState(run?.planned_batches ?? "1");
  const [location, setLocation] = useState(run?.location_id ?? null);
  const [deadline, setDeadline] = useState(run?.deadline_on ?? "");
  const [expires, setExpires] = useState(run?.expires_on ?? "");
  const [assignee, setAssignee] = useState(run?.assignee_ref ?? null);
  const [labour, setLabour] = useState(run?.labour_minutes?.toString() ?? "");
  const [startNow, setStartNow] = useState(false);
  // Making as much as one line allows: that line, the amount to use (what is
  // usable when blank) and its unit (its item's tracking unit when null).
  const [asMuch, setAsMuch] = useState(false);
  const [useItem, setUseItem] = useState<number | null>(null);
  const [useAmount, setUseAmount] = useState("");
  const [useUnit, setUseUnit] = useState<number | null>(null);
  const use =
    asMuch && useItem !== null
      ? { item_id: useItem, amount: isPositive(useAmount) ? useAmount : null, unit_id: useUnit }
      : null;
  const planned = useDebouncedValue<RunPlan | null>(
    run || item === null
      ? null
      : {
          item_id: item,
          location_id: location,
          planned_batches: isPositive(batches) ? batches : "1",
          use,
        },
    300
  );
  const preview = useQuery({
    queryKey: ["runs", "preview", planned],
    queryFn: ({ signal }) => previewRun(planned as RunPlan, undefined, signal),
    enabled: planned !== null,
    placeholderData: keepPreviousData,
  });
  const create = useCreateRun();
  const update = useUpdateRun();
  const saving = create.isPending || update.isPending;
  const completed = run?.status === "completed";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const fields = {
      ...(code.trim() ? { code: code.trim() } : {}),
      ...(completed || use ? {} : { planned_batches: batches }),
      ...(completed || location === null ? {} : { location_id: location }),
      deadline_on: deadline || null,
      expires_on: expires || null,
      assignee_ref: assignee,
      labour_minutes: labour ? Number(labour) : null,
    };
    try {
      let saved: RunRead;
      if (run) saved = await update.mutateAsync({ runId: run.id, data: fields });
      else if (item === null) return;
      else
        saved = await create.mutateAsync({
          data: {
            ...fields,
            item_id: item,
            start: startNow,
            use: use && { ...use, amount: use.amount ?? preview.data?.use?.amount },
          },
        });
      queries.setQueryData(getReadRunQueryKey(saved.id), saved);
      void queries.invalidateQueries({ queryKey: getListRunsQueryKey() });
      onSaved(saved);
    } catch (error) {
      toast.error(errorMessage(error, t, "runs.saveFailed"));
    }
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogBody className="space-y-4">
        {!run && (
          <ItemField
            label={t("runs.making")}
            kinds={["base", "variation"]}
            value={item}
            onChange={setItem}
          />
        )}
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("runs.code")} hint={run ? undefined : t("runs.codeHint")}>
            <Input value={code} onChange={(event) => setCode(event.target.value)} />
          </Field>
          {!asMuch && (
            <Field label={t("runs.batches")}>
              <Input
                inputMode="decimal"
                required
                disabled={completed}
                value={batches}
                onChange={(event) => setBatches(event.target.value)}
              />
            </Field>
          )}
        </div>
        {!completed && <LocationField value={location} onChange={setLocation} />}
        {!run && item !== null && (
          <Field label={t("runs.plan.asMuch")} hint={t("runs.plan.asMuchHint")}>
            <Switch checked={asMuch} onCheckedChange={setAsMuch} />
          </Field>
        )}
        {!run && preview.data && (
          <RunPlanned
            preview={preview.data}
            asMuch={asMuch}
            useItem={useItem}
            onUseItem={(chosen) => {
              setUseItem(chosen);
              setUseAmount("");
              setUseUnit(null);
            }}
            useAmount={useAmount}
            onUseAmount={setUseAmount}
            useUnit={useUnit}
            onUseUnit={setUseUnit}
          />
        )}
        {!run && preview.isError && (
          <p className="text-sm text-muted-foreground">
            {errorMessage(preview.error, t, "runs.plan.failed")}
          </p>
        )}
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("runs.deadline")}>
            <Input
              type="date"
              value={deadline}
              onChange={(event) => setDeadline(event.target.value)}
            />
          </Field>
          <Field label={t("runs.expires")}>
            <Input
              type="date"
              value={expires}
              onChange={(event) => setExpires(event.target.value)}
            />
          </Field>
        </div>
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("runs.assignee")}>
            <MemberPicker<string>
              searchMembers={searchMembers}
              scope={{ type: "community" }}
              placeholder={t("runs.assigneeChoose")}
              emptyMessage={t("runs.nobody")}
              value={assignee}
              onChange={setAssignee}
            />
          </Field>
          <Field label={t("runs.labour")}>
            <Input
              type="number"
              min={0}
              step={1}
              value={labour}
              onChange={(event) => setLabour(event.target.value)}
            />
          </Field>
        </div>
        {!run && (
          <div className="flex items-center gap-2">
            <Checkbox
              id="run-start-now"
              checked={startNow}
              onCheckedChange={(checked) => setStartNow(checked === true)}
            />
            <Label htmlFor="run-start-now">{t("runs.startNow")}</Label>
          </div>
        )}
      </DialogBody>
      <DialogFooter>
        <Button
          type="submit"
          disabled={
            saving ||
            (!run && item === null) ||
            (asMuch && !run && !(Number(preview.data?.use?.amount) > 0))
          }
        >
          {saving ? t("hoard:common.saving") : t("hoard:common.save")}
        </Button>
      </DialogFooter>
    </form>
  );
}

/**
 * What a new run takes, as Hoard plans it: each line beside what is usable at
 * the run's location, short lines marked, and what it makes. Making as much as
 * one line allows, that line and the amount of it to use, which is what is
 * usable there until another is typed, and the most there is enough for when
 * another line is short.
 */
function RunPlanned({
  preview,
  asMuch,
  useItem,
  onUseItem,
  useAmount,
  onUseAmount,
  useUnit,
  onUseUnit,
}: {
  preview: RunPreview;
  asMuch: boolean;
  useItem: number | null;
  onUseItem: (itemId: number | null) => void;
  useAmount: string;
  onUseAmount: (amount: string) => void;
  useUnit: number | null;
  onUseUnit: (unitId: number | null) => void;
}) {
  const { t } = useTranslation("production");
  const { item } = useMovementLabels();
  const quantity = useQuantity();
  const { unit } = useSettingsNames();
  const used = preview.use;
  const withUnit = (amount: string, unitId: number | null) =>
    [quantity(amount), unitId === null ? "" : unit(unitId)].filter(Boolean).join(" ");
  return (
    <div className="flex flex-col gap-3 rounded-md border p-3 text-sm">
      {asMuch && (
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-48 flex-1">
            <Field label={t("runs.plan.ingredient")}>
              <IdSelect
                value={useItem}
                options={preview.lines.map((line) => ({ id: line.item.id, name: item(line.item) }))}
                onChange={onUseItem}
              />
            </Field>
          </div>
          {used && (
            <>
              <div className="w-28">
                <Field label={t("runs.plan.amount")}>
                  <Input
                    inputMode="decimal"
                    value={useAmount}
                    placeholder={quantity(used.amount)}
                    onChange={(event) => onUseAmount(event.target.value)}
                  />
                </Field>
              </div>
              <div className="w-40">
                <Field label={t("runs.plan.unit")}>
                  <UnitPicker
                    value={useUnit ?? used.unit_id}
                    like={used.unit_id}
                    onChange={onUseUnit}
                  />
                </Field>
              </div>
            </>
          )}
        </div>
      )}
      {used && (
        <p className="text-muted-foreground">
          {Number(used.usable) > 0
            ? t("runs.plan.usable", { quantity: withUnit(used.usable, used.unit_id) })
            : t("runs.plan.nothing")}
          {Number(used.expired) > 0 &&
            ` ${t("runs.plan.expired", { quantity: withUnit(used.expired, used.unit_id) })}`}
        </p>
      )}
      {preview.lines.length > 0 && Number(preview.batches) > 0 && (
        <ul className="flex flex-col gap-1">
          {preview.lines.map((line) => (
            <li key={line.item.id} className="flex flex-wrap items-center justify-between gap-2">
              <span>{item(line.item)}</span>
              <span className="inline-flex items-center gap-2 tabular-nums">
                {line.short && (
                  <Badge variant="warning">
                    {t("runs.lines.short", { quantity: withUnit(line.short, line.unit_id) })}
                  </Badge>
                )}
                {withUnit(line.quantity, line.unit_id)}
              </span>
            </li>
          ))}
        </ul>
      )}
      {Number(preview.batches) > 0 && (
        <p>
          {t("runs.plan.makes", {
            quantity: withUnit(preview.quantity, preview.unit_id),
            batches: quantity(preview.batches),
          })}
        </p>
      )}
      {used?.most && (
        <div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              onUseAmount(used.most ?? "");
              onUseUnit(null);
            }}
          >
            {t("runs.plan.most", { quantity: withUnit(used.most, used.unit_id) })}
          </Button>
        </div>
      )}
    </div>
  );
}
