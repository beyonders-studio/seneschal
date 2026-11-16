import { Link } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import { useListAdjustments } from "@/api/generated/adjustments/adjustments";
import { useListOrders } from "@/api/generated/orders/orders";
import { useListPurchases } from "@/api/generated/purchases/purchases";
import { useListRuns } from "@/api/generated/runs/runs";
import { useListStocktakes } from "@/api/generated/stocktakes/stocktakes";
import { AdjustmentsTable } from "@/features/adjustments/AdjustmentsPage";
import { useMovementLabels } from "@/features/adjustments/labels";
import { OrdersTable } from "@/features/orders/OrdersPage";
import { PurchasesTable } from "@/features/purchases/PurchasesPage";
import { RunsTable } from "@/features/runs/RunsPage";
import { PaginationBar } from "@/ui/pagination-bar";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** How many records a part of an item's history lists a page. */
const PAGE_SIZE = 10;

/** The page of a part of an item's history being read, and what asks for it. */
function useHistoryPage() {
  const [page, setPage] = useState(1);
  return { page, setPage, params: { offset: (page - 1) * PAGE_SIZE, limit: PAGE_SIZE } };
}

/** One page of a part of an item's history, a note when it holds nothing,
 *  and the way to its other pages. */
function HistoryPage<T>({
  list,
  page,
  onPageChange,
  empty,
  children,
}: {
  list: {
    data?: { items: T[]; total: number };
    isError: boolean;
    refetch: () => unknown;
  };
  page: number;
  onPageChange: (page: number) => void;
  empty: string;
  children: (items: T[]) => ReactNode;
}) {
  if (list.isError) return <StatusMessage onRetry={() => void list.refetch()} />;
  if (!list.data) return null;
  if (list.data.total === 0) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <div className="flex flex-col gap-2">
      {children(list.data.items)}
      {list.data.total > PAGE_SIZE && (
        <PaginationBar
          page={page}
          pageSize={PAGE_SIZE}
          totalCount={list.data.total}
          hasNext={page * PAGE_SIZE < list.data.total}
          onPageChange={onPageChange}
        />
      )}
    </div>
  );
}

/** When a stocktake last counted an item, at any location. */
export const LastStocktake = ({ item }: { item: { last_stocktake: string | null } }) => {
  const { date } = useMovementLabels();
  return item.last_stocktake && date(item.last_stocktake);
};

function RunsOf({
  filter,
  title,
  empty,
}: {
  filter: { item_id: number } | { used_item_id: number };
  title?: string;
  empty: string;
}) {
  const { page, setPage, params } = useHistoryPage();
  const runs = useListRuns({ ...params, ...filter });
  return (
    <div className="flex flex-col gap-2">
      {title && <h4 className="text-sm font-semibold">{title}</h4>}
      <HistoryPage list={runs} page={page} onPageChange={setPage} empty={empty}>
        {(items) => <RunsTable runs={items} />}
      </HistoryPage>
    </div>
  );
}

/** The production runs that used an item, and, for one that is `made`, those
 *  that made it. */
export function RunsHistory({ itemId, made = false }: { itemId: number; made?: boolean }) {
  const { t } = useTranslation("catalog");
  if (!made) return <RunsOf filter={{ used_item_id: itemId }} empty={t("history.noRunsUsed")} />;
  return (
    <div className="flex flex-col gap-4">
      <RunsOf
        filter={{ item_id: itemId }}
        title={t("history.runsMade")}
        empty={t("history.noRunsMade")}
      />
      <RunsOf
        filter={{ used_item_id: itemId }}
        title={t("history.runsUsed")}
        empty={t("history.noRunsUsed")}
      />
    </div>
  );
}

/** An item's movements, the latest first. */
export function AdjustmentsHistory({ itemId }: { itemId: number }) {
  const { t } = useTranslation("catalog");
  const { page, setPage, params } = useHistoryPage();
  const adjustments = useListAdjustments({ ...params, item_id: itemId });
  return (
    <HistoryPage
      list={adjustments}
      page={page}
      onPageChange={setPage}
      empty={t("history.noAdjustments")}
    >
      {(items) => <AdjustmentsTable adjustments={items} />}
    </HistoryPage>
  );
}

/** The orders selling an item, a bundle's part included, the latest first. */
export function OrdersHistory({ itemId }: { itemId: number }) {
  const { t } = useTranslation("catalog");
  const { page, setPage, params } = useHistoryPage();
  const orders = useListOrders({ ...params, item_id: itemId });
  return (
    <HistoryPage list={orders} page={page} onPageChange={setPage} empty={t("history.noOrders")}>
      {(items) => <OrdersTable orders={items} />}
    </HistoryPage>
  );
}

/** The purchases buying an item, the latest first. */
export function PurchasesHistory({ itemId }: { itemId: number }) {
  const { t } = useTranslation("catalog");
  const { page, setPage, params } = useHistoryPage();
  const purchases = useListPurchases({ ...params, item_id: itemId });
  return (
    <HistoryPage
      list={purchases}
      page={page}
      onPageChange={setPage}
      empty={t("history.noPurchases")}
    >
      {(items) => <PurchasesTable purchases={items} />}
    </HistoryPage>
  );
}

/** The stocktakes that counted an item, the latest first: what each expected
 *  of it and counted. */
export function StocktakesHistory({ itemId }: { itemId: number }) {
  const { t } = useTranslation("catalog");
  const { date, location, quantity } = useMovementLabels();
  const { page, setPage, params } = useHistoryPage();
  const stocktakes = useListStocktakes({ ...params, item_id: itemId });
  return (
    <HistoryPage
      list={stocktakes}
      page={page}
      onPageChange={setPage}
      empty={t("history.noStocktakes")}
    >
      {(items) => (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("hoard:common.date")}</TableHead>
              <TableHead>{t("hoard:common.location")}</TableHead>
              <TableHead className="text-right">{t("history.expected")}</TableHead>
              <TableHead className="text-right">{t("history.counted")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((stocktake) => (
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
                <TableCell>{location(stocktake.location_id)}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {stocktake.count?.expected && quantity(stocktake.count.expected)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {stocktake.count?.counted
                    ? quantity(stocktake.count.counted)
                    : t("history.notCounted")}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </HistoryPage>
  );
}
