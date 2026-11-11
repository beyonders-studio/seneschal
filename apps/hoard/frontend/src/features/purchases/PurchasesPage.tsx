import { Link, useNavigate } from "@tanstack/react-router";
import { Plus, ShoppingCart } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import { useListPurchases } from "@/api/generated/purchases/purchases";
import { useMovementLabels } from "@/features/adjustments/labels";
import { useSupplierNames } from "@/features/contacts/SupplierPicker";
import { PurchaseDialog } from "@/features/purchases/PurchaseDialog";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { useSession } from "@/session";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";
import { PaginationBar } from "@/ui/pagination-bar";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** How many purchases a page lists. */
export const PAGE_SIZE = 50;

/** The purchases list, under Stock: the latest first, and adding one. */
export function PurchasesPage() {
  const { t } = useTranslation("movements");
  const navigate = useNavigate();
  const { read_only } = useSession();
  const seesCosts = useSeesCosts();
  const money = useMoney();
  const { date, location } = useMovementLabels();
  const supplier = useSupplierNames();
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);
  const purchases = useListPurchases({ offset: (page - 1) * PAGE_SIZE, limit: PAGE_SIZE });

  const add = (
    <Button onClick={() => setAdding(true)} disabled={read_only}>
      <Plus aria-hidden />
      {t("purchases.add")}
    </Button>
  );

  let body: ReactNode;
  if (purchases.isError) body = <StatusMessage onRetry={() => void purchases.refetch()} />;
  else if (!purchases.data) body = <p className="text-muted-foreground">{t("hoard:common.loading")}</p>;
  else if (purchases.data.total === 0)
    body = (
      <StatusMessage
        icon={<ShoppingCart />}
        title={t("purchases.emptyTitle")}
        description={t("purchases.emptyBody")}
        action={add}
      />
    );
  else
    body = (
      <>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("purchases.code")}</TableHead>
              <TableHead>{t("hoard:common.date")}</TableHead>
              <TableHead>{t("connection:suppliers.label")}</TableHead>
              <TableHead>{t("hoard:common.location")}</TableHead>
              <TableHead>{t("purchases.received")}</TableHead>
              {seesCosts && <TableHead className="text-right">{t("hoard:common.total")}</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {purchases.data.items.map((purchase) => (
              <TableRow key={purchase.id}>
                <TableCell>
                  <Link
                    to="/stock/purchases/$purchaseId"
                    params={{ purchaseId: String(purchase.id) }}
                    className="font-medium underline-offset-4 hover:underline"
                  >
                    {purchase.code}
                  </Link>
                  {purchase.paid && (
                    <Badge variant="secondary" className="ml-2">
                      {t("purchases.paid")}
                    </Badge>
                  )}
                </TableCell>
                <TableCell>{date(purchase.dated_on)}</TableCell>
                <TableCell>{supplier(purchase.supplier_contact_id)}</TableCell>
                <TableCell>{location(purchase.location_id)}</TableCell>
                <TableCell>
                  {t("purchases.receivedOf", { received: purchase.received, lines: purchase.lines })}
                </TableCell>
                {seesCosts && (
                  <TableCell className="text-right">{money(purchase.total ?? "0")}</TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {purchases.data.total > PAGE_SIZE && (
          <PaginationBar
            page={page}
            pageSize={PAGE_SIZE}
            totalCount={purchases.data.total}
            hasNext={page * PAGE_SIZE < purchases.data.total}
            onPageChange={setPage}
          />
        )}
      </>
    );

  return (
    <section className="flex flex-col gap-4">
      {purchases.data?.total ? <div className="flex justify-end">{add}</div> : null}
      {body}
      <PurchaseDialog
        open={adding}
        onOpenChange={setAdding}
        onSaved={(saved) =>
          void navigate({
            to: "/stock/purchases/$purchaseId",
            params: { purchaseId: String(saved.id) },
          })
        }
      />
    </section>
  );
}
