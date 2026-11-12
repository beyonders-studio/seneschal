import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useListStorePushes } from "@/api/generated/stores/stores";
import { useStoreReason } from "@/features/stores/words";
import { Badge } from "@/ui/badge";
import { PaginationBar } from "@/ui/pagination-bar";
import { RelativeTime } from "@/ui/relative-time";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** How many pushes a page of the log lists. */
const PAGE_SIZE = 20;

/** What Hoard told a store it may sell, newest first: each push, how it
 *  stands, and why one was not taken. */
export function PushLog({ storeId }: { storeId: number }) {
  const { t } = useTranslation("stores");
  const reason = useStoreReason();
  const [page, setPage] = useState(1);
  const pushes = useListStorePushes(storeId, {
    offset: (page - 1) * PAGE_SIZE,
    limit: PAGE_SIZE,
  });

  if (pushes.isError) return <StatusMessage onRetry={() => void pushes.refetch()} />;
  if (!pushes.data) return <p className="text-muted-foreground">{t("hoard:common.loading")}</p>;
  if (pushes.data.total === 0)
    return <p className="text-sm text-muted-foreground">{t("push.empty")}</p>;
  return (
    <>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("push.listing")}</TableHead>
            <TableHead>{t("push.location")}</TableHead>
            <TableHead className="text-right">{t("push.quantity")}</TableHead>
            <TableHead>{t("push.status")}</TableHead>
            <TableHead>{t("push.when")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {pushes.data.items.map((pushed) => (
            <TableRow key={pushed.id}>
              <TableCell>{[pushed.title, pushed.sku].filter(Boolean).join(" · ")}</TableCell>
              <TableCell>{pushed.location}</TableCell>
              <TableCell className="text-right tabular-nums">{pushed.quantity}</TableCell>
              <TableCell>
                <Badge variant={pushed.status === "failed" ? "destructive" : "secondary"}>
                  {t(`push.statuses.${pushed.status}`)}
                </Badge>
                {pushed.error && (
                  <p className="mt-1 text-xs text-muted-foreground">{reason(pushed.error)}</p>
                )}
              </TableCell>
              <TableCell>
                <RelativeTime date={pushed.pushed_at ?? pushed.created_at} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {pushes.data.total > PAGE_SIZE && (
        <PaginationBar
          page={page}
          pageSize={PAGE_SIZE}
          totalCount={pushes.data.total}
          hasNext={page * PAGE_SIZE < pushes.data.total}
          onPageChange={setPage}
        />
      )}
    </>
  );
}
