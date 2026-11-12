import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import type { RunRead } from "@/api/generated/hoard.schemas";
import { listMemberNames, searchMemberNames } from "@/api/generated/members/members";
import {
  getListRunsQueryKey,
  getReadRunQueryKey,
  useCreateRun,
  useUpdateRun,
} from "@/api/generated/runs/runs";
import { toast } from "@/components/Toaster";
import { ItemField, LocationField } from "@/features/adjustments/fields";
import { errorMessage } from "@/lib/errors";
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
import { type Member, MemberPicker, type SearchMembers } from "@/ui/member-picker";

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
  const create = useCreateRun();
  const update = useUpdateRun();
  const saving = create.isPending || update.isPending;
  const completed = run?.status === "completed";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const fields = {
      ...(code.trim() ? { code: code.trim() } : {}),
      ...(completed ? {} : { planned_batches: batches }),
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
        saved = await create.mutateAsync({ data: { ...fields, item_id: item, start: startNow } });
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
        {!run && <ItemField label={t("runs.making")} value={item} onChange={setItem} />}
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("runs.code")} hint={run ? undefined : t("runs.codeHint")}>
            <Input value={code} onChange={(event) => setCode(event.target.value)} />
          </Field>
          <Field label={t("runs.batches")}>
            <Input
              inputMode="decimal"
              required
              disabled={completed}
              value={batches}
              onChange={(event) => setBatches(event.target.value)}
            />
          </Field>
        </div>
        {!completed && <LocationField value={location} onChange={setLocation} />}
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
        <Button type="submit" disabled={saving || (!run && item === null)}>
          {saving ? t("hoard:common.saving") : t("hoard:common.save")}
        </Button>
      </DialogFooter>
    </form>
  );
}
