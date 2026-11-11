import { Link, useNavigate } from "@tanstack/react-router";
import { ClipboardList, Plus } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import type { StocktakeSummary } from "@/api/generated/hoard.schemas";
import { useListStocktakes } from "@/api/generated/stocktakes/stocktakes";
import { useMovementLabels } from "@/features/adjustments/labels";
import { StocktakeDialog } from "@/features/stocktakes/StocktakeDialog";
import { useSession } from "@/session";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";
import { numberFormat } from "@/ui/lib/intl";
import { PaginationBar } from "@/ui/pagination-bar";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** How many stocktakes a page lists. */
const PAGE_SIZE = 50;

/** How far a stocktake has got, and how accurate it was: "3 of 5 counted ·
 *  67% accurate", in the reader's words. */
export function useStocktakeProgress(): (stocktake: StocktakeSummary) => string {
  const { t, i18n } = useTranslation("movements");
  const percent = numberFormat(i18n.resolvedLanguage, {
    style: "percent",
    maximumFractionDigits: 1,
  });
  return (stocktake) => {
    const counted = t("stocktakes.counted", {
      confirmed: stocktake.confirmed_count,
      count: stocktake.row_count,
    });
    return stocktake.accuracy === null || stocktake.accuracy === undefined
      ? counted
      : t("stocktakes.progress", {
          counted,
          accuracy: percent.format(stocktake.accuracy as Intl.StringNumericLiteral),
        });
  };
}

/** The stocktakes list, under Stock: the latest first, and starting one. */
export function StocktakesPage() {
  const { t } = useTranslation("movements");
  const navigate = useNavigate();
  const { read_only } = useSession();
  const { date, location, category } = useMovementLabels();
  const progress = useStocktakeProgress();
  const [page, setPage] = useState(1);
  const [starting, setStarting] = useState(false);
  const stocktakes = useListStocktakes({ offset: (page - 1) * PAGE_SIZE, limit: PAGE_SIZE });

  const start = (
    <Button onClick={() => setStarting(true)} disabled={read_only}>
      <Plus aria-hidden />
      {t("stocktakes.add")}
    </Button>
  );

  let body: ReactNode;
  if (stocktakes.isError) body = <StatusMessage onRetry={() => void stocktakes.refetch()} />;
  else if (!stocktakes.data)
    body = <p className="text-muted-foreground">{t("hoard:common.loading")}</p>;
  else if (stocktakes.data.total === 0)
    body = (
      <StatusMessage
        icon={<ClipboardList />}
        title={t("stocktakes.emptyTitle")}
        description={t("stocktakes.emptyBody")}
        action={start}
      />
    );
  else
    body = (
      <>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("hoard:common.date")}</TableHead>
              <TableHead>{t("stocktakes.kind")}</TableHead>
              <TableHead>{t("hoard:common.location")}</TableHead>
              <TableHead>{t("stocktakes.progressLabel")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {stocktakes.data.items.map((stocktake) => (
              <TableRow key={stocktake.id}>
                <TableCell>
                  <Link
                    to="/stock/stocktakes/$stocktakeId"
                    params={{ stocktakeId: String(stocktake.id) }}
                    className="font-medium underline-offset-4 hover:underline"
                  >
                    {date(stocktake.dated_on)}
                  </Link>
                </TableCell>
                <TableCell>
                  {[
                    t(`stocktakes.kinds.${stocktake.kind}`),
                    category(stocktake.category_id),
                    stocktake.sample_size === null
                      ? ""
                      : t("stocktakes.sample", { count: stocktake.sample_size }),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </TableCell>
                <TableCell>{location(stocktake.location_id)}</TableCell>
                <TableCell>
                  {progress(stocktake)}
                  {stocktake.finished && (
                    <Badge variant="secondary" className="ml-2">
                      {t("stocktakes.finished")}
                    </Badge>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {stocktakes.data.total > PAGE_SIZE && (
          <PaginationBar
            page={page}
            pageSize={PAGE_SIZE}
            totalCount={stocktakes.data.total}
            hasNext={page * PAGE_SIZE < stocktakes.data.total}
            onPageChange={setPage}
          />
        )}
      </>
    );

  return (
    <section className="flex flex-col gap-4">
      {stocktakes.data?.total ? <div className="flex justify-end">{start}</div> : null}
      {body}
      <StocktakeDialog
        open={starting}
        onOpenChange={setStarting}
        onStarted={(started) =>
          void navigate({
            to: "/stock/stocktakes/$stocktakeId",
            params: { stocktakeId: String(started.id) },
          })
        }
      />
    </section>
  );
}
