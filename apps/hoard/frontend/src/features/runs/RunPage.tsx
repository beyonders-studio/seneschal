import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { CircleCheck, Pencil, Play, RotateCcw, Trash2, TriangleAlert } from "lucide-react";
import { type FormEvent, type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import { getListAdjustmentsQueryKey } from "@/api/generated/adjustments/adjustments";
import type { RunLineRead, RunLot, RunRead } from "@/api/generated/hoard.schemas";
import {
  getListRunsQueryKey,
  getReadRunQueryKey,
  useCompleteProductionRun,
  useDeleteProductionRun,
  useReadRun,
  useReopenRun,
  useStartRun,
  useUpdateRunLine,
} from "@/api/generated/runs/runs";
import { toast } from "@/components/Toaster";
import { today, useMovementLabels } from "@/features/adjustments/labels";
import { ItemFacts } from "@/features/items/ItemHeader";
import { RunDialog } from "@/features/runs/RunDialog";
import { RunBadges } from "@/features/runs/RunsPage";
import { errorMessage } from "@/lib/errors";
import { useMemberNames } from "@/lib/members";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { useSession } from "@/session";
import { Alert, AlertDescription, AlertTitle } from "@/ui/alert";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";
import { ConfirmDialog } from "@/ui/confirm-dialog";
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
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** The lot choice that leaves a line to have one assumed. */
const ASSUMED = "assumed";

/** One production run, under Production: what it makes and consumes, from
 *  which lots, starting, completing and reopening it, and what it cost for a
 *  member who sees costs. */
export function RunPage({ runId }: { runId: number }) {
  const { t } = useTranslation("production");
  const run = useReadRun(runId);
  if (run.isError)
    return (
      <StatusMessage
        description={errorMessage(run.error, t, "runs.loadFailed")}
        backTo="/production/runs"
        backLabel={t("runs.back")}
      />
    );
  if (!run.data) return <p className="text-muted-foreground">{t("hoard:common.loading")}</p>;
  return <Run run={run.data} />;
}

function Run({ run }: { run: RunRead }) {
  const { t } = useTranslation("production");
  const queries = useQueryClient();
  const navigate = useNavigate();
  const { read_only } = useSession();
  const seesCosts = useSeesCosts();
  const money = useMoney();
  const { date, location, item, quantity } = useMovementLabels();
  const memberName = useMemberNames([run.assignee_ref, run.created_by]);
  const [editing, setEditing] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [reopening, setReopening] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const start = useStartRun();
  const reopen = useReopenRun();
  const remove = useDeleteProductionRun();
  const updateLine = useUpdateRunLine();
  const completed = run.status === "completed";
  const costed = seesCosts && completed;

  const saved = (next: RunRead) => {
    queries.setQueryData(getReadRunQueryKey(next.id), next);
    void queries.invalidateQueries({ queryKey: getListRunsQueryKey() });
    void queries.invalidateQueries({ queryKey: getListAdjustmentsQueryKey() });
  };
  const change = async (making: Promise<RunRead>) => {
    try {
      saved(await making);
      return true;
    } catch (error) {
      toast.error(errorMessage(error, t, "runs.saveFailed"));
      return false;
    }
  };
  const lotName = (lot: RunLot) =>
    lot.expires_on
      ? t("runs.lines.lotExpires", {
          number: lot.number,
          date: date(lot.expires_on),
          balance: quantity(lot.balance),
        })
      : t("runs.lines.lotOption", { number: lot.number, balance: quantity(lot.balance) });

  const costFacts: [string, ReactNode][] = costed
    ? [
        [t("runs.cost"), money(run.cost ?? "0")],
        [t("runs.unitCost"), run.unit_cost && money(run.unit_cost, { exact: true })],
      ]
    : [];

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <h2 className="text-xl font-semibold tracking-tight">
            {t("runs.title", { code: run.code })}
          </h2>
          <p className="text-sm text-muted-foreground">{item(run.item)}</p>
          <RunBadges run={run} />
          <p className="text-xs text-muted-foreground">
            {t("hoard:common.createdBy", { name: memberName(run.created_by) })}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {run.status === "not_started" && (
            <Button
              disabled={read_only || start.isPending}
              onClick={() => void change(start.mutateAsync({ runId: run.id, data: {} }))}
            >
              <Play aria-hidden />
              {t("runs.start")}
            </Button>
          )}
          {completed ? (
            <Button variant="outline" disabled={read_only} onClick={() => setReopening(true)}>
              <RotateCcw aria-hidden />
              {t("runs.reopen")}
            </Button>
          ) : (
            <Button disabled={read_only} onClick={() => setCompleting(true)}>
              <CircleCheck aria-hidden />
              {t("runs.complete")}
            </Button>
          )}
          <Button variant="outline" onClick={() => setEditing(true)} disabled={read_only}>
            <Pencil aria-hidden />
            {t("hoard:common.edit")}
          </Button>
          {!completed && (
            <Button variant="outline" onClick={() => setDeleting(true)} disabled={read_only}>
              <Trash2 aria-hidden />
              {t("hoard:common.delete")}
            </Button>
          )}
        </div>
      </div>

      {run.uncosted && (
        <Alert>
          <TriangleAlert aria-hidden />
          <AlertTitle>{t("runs.uncostedTitle")}</AlertTitle>
          <AlertDescription>{t("runs.uncostedBody")}</AlertDescription>
        </Alert>
      )}

      <ItemFacts
        facts={[
          [t("runs.batches"), quantity(run.planned_batches)],
          [t("runs.planned"), run.planned_quantity && quantity(run.planned_quantity)],
          [t("runs.actual"), run.actual_quantity && quantity(run.actual_quantity)],
          [t("hoard:common.location"), location(run.location_id)],
          [t("runs.assignee"), run.assignee_ref && memberName(run.assignee_ref)],
          [t("runs.started"), run.started_on && date(run.started_on)],
          [t("runs.deadline"), run.deadline_on && date(run.deadline_on)],
          [t("runs.completed"), run.completed_on && date(run.completed_on)],
          [t("runs.expires"), run.expires_on && date(run.expires_on)],
          [t("runs.outputLot"), run.output_lot?.number],
          [t("runs.labour"), run.labour_minutes?.toString()],
          ...costFacts,
        ]}
      />

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("runs.lines.item")}</TableHead>
            <TableHead className="text-right">{t("hoard:common.quantity")}</TableHead>
            <TableHead>{t("runs.lines.lot")}</TableHead>
            {costed && (
              <>
                <TableHead className="text-right">{t("hoard:common.unitCost")}</TableHead>
                <TableHead className="text-right">{t("hoard:common.cost")}</TableHead>
              </>
            )}
          </TableRow>
        </TableHeader>
        <TableBody>
          {run.lines.map((line) => (
            <TableRow key={line.id}>
              <TableCell>{item(line.item)}</TableCell>
              <TableCell className="text-right">{quantity(line.quantity)}</TableCell>
              <TableCell>
                {completed || read_only ? (
                  <LotShown line={line} name={lotName} />
                ) : (
                  <Select
                    value={line.lot ? String(line.lot.id) : ASSUMED}
                    onValueChange={(next) =>
                      void change(
                        updateLine.mutateAsync({
                          runId: run.id,
                          lineId: line.id,
                          data: { lot_id: next === ASSUMED ? null : Number(next) },
                        })
                      )
                    }
                  >
                    <SelectTrigger aria-label={t("runs.lines.lot")} className="min-w-48">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={ASSUMED}>{t("runs.lines.assumeLot")}</SelectItem>
                      {[
                        ...line.lots,
                        ...(line.lot && !line.lots.some((lot) => lot.id === line.lot?.id)
                          ? [line.lot]
                          : []),
                      ].map((lot) => (
                        <SelectItem key={lot.id} value={String(lot.id)}>
                          {lotName(lot)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              </TableCell>
              {costed && (
                <>
                  <TableCell className="text-right">
                    {line.unit_cost && money(line.unit_cost, { exact: true })}
                  </TableCell>
                  <TableCell className="text-right">{line.cost && money(line.cost)}</TableCell>
                </>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {run.lines.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("runs.lines.empty")}</p>
      )}

      <RunDialog open={editing} onOpenChange={setEditing} run={run} />
      <CompleteDialog
        run={run}
        open={completing}
        onOpenChange={setCompleting}
        onSaved={(next) => {
          saved(next);
          setCompleting(false);
        }}
      />
      <ConfirmDialog
        open={reopening}
        onOpenChange={setReopening}
        title={t("runs.reopenTitle", { code: run.code })}
        description={t("runs.reopenBody", { code: run.code })}
        confirmLabel={t("runs.reopen")}
        isLoading={reopen.isPending}
        onConfirm={async () => {
          await change(reopen.mutateAsync({ runId: run.id }));
          setReopening(false);
        }}
      />
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={t("runs.deleteTitle", { code: run.code })}
        description={t("runs.deleteBody")}
        confirmLabel={t("hoard:common.delete")}
        destructive
        isLoading={remove.isPending}
        onConfirm={async () => {
          try {
            await remove.mutateAsync({ runId: run.id });
            void queries.invalidateQueries({ queryKey: getListRunsQueryKey() });
            void navigate({ to: "/production/runs" });
          } catch (error) {
            setDeleting(false);
            toast.error(errorMessage(error, t, "runs.deleteFailed"));
          }
        }}
      />
    </section>
  );
}

/** The lot a line consumes, and whether Hoard assumed it. */
function LotShown({ line, name }: { line: RunLineRead; name: (lot: RunLot) => string }) {
  const { t } = useTranslation("production");
  if (!line.lot) return <span className="text-muted-foreground">{t("runs.lines.assumeLot")}</span>;
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      {name(line.lot)}
      {line.assumed && <Badge variant="secondary">{t("runs.lines.assumed")}</Badge>}
    </span>
  );
}

/** Asks what a run made, and when, and completes it. */
function CompleteDialog({
  run,
  open,
  onOpenChange,
  onSaved,
}: {
  run: RunRead;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (saved: RunRead) => void;
}) {
  const { t } = useTranslation("production");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("runs.completeTitle", { code: run.code })}</DialogTitle>
        </DialogHeader>
        <CompleteForm run={run} onSaved={onSaved} />
      </DialogContent>
    </Dialog>
  );
}

function CompleteForm({ run, onSaved }: { run: RunRead; onSaved: (saved: RunRead) => void }) {
  const { t } = useTranslation("production");
  const [made, setMade] = useState(run.planned_quantity ?? "");
  const [completedOn, setCompletedOn] = useState(today());
  const complete = useCompleteProductionRun();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      onSaved(
        await complete.mutateAsync({
          runId: run.id,
          data: { actual_quantity: made, completed_on: completedOn },
        })
      );
    } catch (error) {
      toast.error(errorMessage(error, t, "runs.saveFailed"));
    }
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogBody className="space-y-4">
        <p className="text-sm text-muted-foreground">
          {t("runs.completeBody", { code: run.code })}
        </p>
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("runs.actual")}>
            <Input
              inputMode="decimal"
              required
              value={made}
              onChange={(event) => setMade(event.target.value)}
            />
          </Field>
          <Field label={t("runs.completedOn")}>
            <Input
              type="date"
              required
              value={completedOn}
              onChange={(event) => setCompletedOn(event.target.value)}
            />
          </Field>
        </div>
      </DialogBody>
      <DialogFooter>
        <Button type="submit" disabled={complete.isPending}>
          {complete.isPending ? t("hoard:common.saving") : t("runs.complete")}
        </Button>
      </DialogFooter>
    </form>
  );
}
