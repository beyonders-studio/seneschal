import { Link, useNavigate } from "@tanstack/react-router";
import { Plus, ShoppingCart } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import { OrderStatus, type OrderSummary } from "@/api/generated/hoard.schemas";
import { useListOrders } from "@/api/generated/orders/orders";
import { useMovementLabels } from "@/features/adjustments/labels";
import { useContactNames } from "@/features/contacts/CustomerPicker";
import { OrderDialog } from "@/features/orders/OrderDialog";
import { useMoney } from "@/lib/money";
import { useSession } from "@/session";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";
import { PaginationBar } from "@/ui/pagination-bar";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** How many orders a page lists. */
const PAGE_SIZE = 50;
/** The filter that shows every order. */
const ALL = "all";

const isStatus = (value: string): value is OrderStatus => value in OrderStatus;

/** An order's status, and whether it is paid. */
export function OrderBadges({ order }: { order: Pick<OrderSummary, "status" | "paid"> }) {
  const { t } = useTranslation("orders");
  return (
    <span className="inline-flex flex-wrap gap-2">
      <Badge variant={order.status === "open" ? "default" : "secondary"}>
        {t(`status.${order.status}`)}
      </Badge>
      {order.paid && <Badge variant="outline">{t("order.paid")}</Badge>}
    </span>
  );
}

/** Who an order is for: its customer, or the store's number for it. */
export function useOrderFor(): (
  order: Pick<OrderSummary, "customer_contact_id" | "external_number">
) => string {
  const { t } = useTranslation("orders");
  const contact = useContactNames();
  return (order) =>
    order.customer_contact_id === null
      ? t("order.storeNumber", { number: order.external_number ?? "" })
      : contact(order.customer_contact_id);
}

/** The orders list, under Orders: the latest first, filtered by status, and
 *  adding one by hand. */
export function OrdersPage() {
  const { t } = useTranslation("orders");
  const navigate = useNavigate();
  const { read_only } = useSession();
  const money = useMoney();
  const { date, location } = useMovementLabels();
  const orderFor = useOrderFor();
  const [status, setStatus] = useState<OrderStatus | null>(null);
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);
  const orders = useListOrders({
    offset: (page - 1) * PAGE_SIZE,
    limit: PAGE_SIZE,
    ...(status ? { status } : {}),
  });

  const add = (
    <Button onClick={() => setAdding(true)} disabled={read_only}>
      <Plus aria-hidden />
      {t("list.add")}
    </Button>
  );

  let body: ReactNode;
  if (orders.isError) body = <StatusMessage onRetry={() => void orders.refetch()} />;
  else if (!orders.data)
    body = <p className="text-muted-foreground">{t("hoard:common.loading")}</p>;
  else if (orders.data.total === 0 && !status)
    body = (
      <StatusMessage
        icon={<ShoppingCart />}
        title={t("list.emptyTitle")}
        description={t("list.emptyBody")}
        action={add}
      />
    );
  else
    body = (
      <>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("order.code")}</TableHead>
              <TableHead>{t("order.orderedOn")}</TableHead>
              <TableHead>{t("customers.label")}</TableHead>
              <TableHead>{t("hoard:common.location")}</TableHead>
              <TableHead>{t("list.status")}</TableHead>
              <TableHead className="text-right">{t("hoard:common.total")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {orders.data.items.map((order) => (
              <TableRow key={order.id}>
                <TableCell>
                  <Link
                    to="/orders/$orderId"
                    params={{ orderId: String(order.id) }}
                    className="font-medium underline-offset-4 hover:underline"
                  >
                    {order.code}
                  </Link>
                </TableCell>
                <TableCell>{date(order.ordered_on)}</TableCell>
                <TableCell>{orderFor(order)}</TableCell>
                <TableCell>{location(order.location_id)}</TableCell>
                <TableCell>
                  <OrderBadges order={order} />
                </TableCell>
                <TableCell className="text-right">{money(order.total)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {orders.data.items.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("list.noneOfStatus")}</p>
        )}
        {orders.data.total > PAGE_SIZE && (
          <PaginationBar
            page={page}
            pageSize={PAGE_SIZE}
            totalCount={orders.data.total}
            hasNext={page * PAGE_SIZE < orders.data.total}
            onPageChange={setPage}
          />
        )}
      </>
    );

  return (
    <section className="flex flex-col gap-4">
      {orders.data?.total || status ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Select
            value={status ?? ALL}
            onValueChange={(next) => {
              setStatus(isStatus(next) ? next : null);
              setPage(1);
            }}
          >
            <SelectTrigger className="w-48" aria-label={t("list.filter")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("list.all")}</SelectItem>
              {Object.values(OrderStatus).map((one) => (
                <SelectItem key={one} value={one}>
                  {t(`status.${one}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {add}
        </div>
      ) : null}
      {body}
      <OrderDialog
        open={adding}
        onOpenChange={setAdding}
        onSaved={(saved) =>
          void navigate({ to: "/orders/$orderId", params: { orderId: String(saved.id) } })
        }
      />
    </section>
  );
}
