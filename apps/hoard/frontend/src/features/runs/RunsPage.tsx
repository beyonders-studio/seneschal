import { Link, useNavigate } from "@tanstack/react-router";
import { Factory, Plus } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import { RunStatus, type RunSummary } from "@/api/generated/hoard.schemas";
import { useListRuns } from "@/api/generated/runs/runs";
import { useMovementLabels } from "@/features/adjustments/labels";
import { RunDialog } from "@/features/runs/RunDialog";
import { useMemberNames } from "@/lib/members";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { useSession } from "@/session";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";
import { PaginationBar } from "@/ui/pagination-bar";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** How many runs a page lists. */
const PAGE_SIZE = 50;
/** The filter that shows every run. */
const ALL = "all";

const isStatus = (value: string): value is RunStatus => value in RunStatus;

/** A run's status, and whether it is uncosted. */
export function RunBadges({ run }: { run: Pick<RunSummary, "status" | "uncosted"> }) {
  const { t } = useTranslation("production");
  return (
    <span className="inline-flex flex-wrap gap-2">
      <Badge variant={run.status === "completed" ? "outline" : "secondary"}>
        {t(`runs.status.${run.status}`)}
      </Badge>
      {run.uncosted && <Badge variant="warning">{t("runs.uncosted")}</Badge>}
    </span>
  );
}

/** The production runs list, under Production: those not completed first,
 *  filtered by status, and opening one. */
export function RunsPage() {
  const { t } = useTranslation("production");
  const navigate = useNavigate();
  const { read_only } = useSession();
  const seesCosts = useSeesCosts();
  const money = useMoney();
  const { date, item, quantity } = useMovementLabels();
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);
  const runs = useListRuns({
    offset: (page - 1) * PAGE_SIZE,
    limit: PAGE_SIZE,
    ...(status ? { status } : {}),
  });
  const memberName = useMemberNames(runs.data?.items.map((run) => run.assignee_ref) ?? []);

  const add = (
    <Button onClick={() => setAdding(true)} disabled={read_only}>
      <Plus aria-hidden />
      {t("runs.add")}
    </Button>
  );

  let body: ReactNode;
  if (runs.isError) body = <StatusMessage onRetry={() => void runs.refetch()} />;
  else if (!runs.data) body = <p className="text-muted-foreground">{t("hoard:common.loading")}</p>;
  else if (runs.data.total === 0 && !status)
    body = (
      <StatusMessage
        icon={<Factory />}
        title={t("runs.emptyTitle")}
        description={t("runs.emptyBody")}
        action={add}
      />
    );
  else
    body = (
      <>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("runs.code")}</TableHead>
              <TableHead>{t("runs.making")}</TableHead>
              <TableHead>{t("runs.statusLabel")}</TableHead>
              <TableHead className="text-right">{t("runs.planned")}</TableHead>
              <TableHead className="text-right">{t("runs.actual")}</TableHead>
              <TableHead>{t("runs.deadline")}</TableHead>
              <TableHead>{t("runs.assignee")}</TableHead>
              {seesCosts && <TableHead className="text-right">{t("hoard:common.cost")}</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.data.items.map((run) => (
              <TableRow key={run.id}>
                <TableCell>
                  <Link
                    to="/production/runs/$runId"
                    params={{ runId: String(run.id) }}
                    className="font-medium underline-offset-4 hover:underline"
                  >
                    {run.code}
                  </Link>
                </TableCell>
                <TableCell>{item(run.item)}</TableCell>
                <TableCell>
                  <RunBadges run={run} />
                </TableCell>
                <TableCell className="text-right">
                  {quantity(run.planned_quantity ?? run.planned_batches)}
                </TableCell>
                <TableCell className="text-right">
                  {run.actual_quantity === null ? "" : quantity(run.actual_quantity)}
                </TableCell>
                <TableCell>{run.deadline_on ? date(run.deadline_on) : ""}</TableCell>
                <TableCell>{run.assignee_ref ? memberName(run.assignee_ref) : ""}</TableCell>
                {seesCosts && (
                  <TableCell className="text-right">{run.cost ? money(run.cost) : ""}</TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {runs.data.total > PAGE_SIZE && (
          <PaginationBar
            page={page}
            pageSize={PAGE_SIZE}
            totalCount={runs.data.total}
            hasNext={page * PAGE_SIZE < runs.data.total}
            onPageChange={setPage}
          />
        )}
      </>
    );

  return (
    <section className="flex flex-col gap-4">
      {runs.data?.total || status ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Select
            value={status ?? ALL}
            onValueChange={(next) => {
              setStatus(isStatus(next) ? next : null);
              setPage(1);
            }}
          >
            <SelectTrigger className="w-48" aria-label={t("runs.filter")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("runs.all")}</SelectItem>
              {Object.values(RunStatus).map((one) => (
                <SelectItem key={one} value={one}>
                  {t(`runs.status.${one}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {add}
        </div>
      ) : null}
      {body}
      <RunDialog
        open={adding}
        onOpenChange={setAdding}
        onSaved={(saved) =>
          void navigate({ to: "/production/runs/$runId", params: { runId: String(saved.id) } })
        }
      />
    </section>
  );
}
