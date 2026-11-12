import { createFileRoute, Outlet, useLocation, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { HoardPage } from "@/components/HoardPage";
import { Tabs, TabsBar, TabsTrigger } from "@/ui/tabs";

/** Each of Orders' tabs, and the page it opens. */
const TABS = {
  orders: "/orders",
  listings: "/orders/listings",
} as const;

type Tab = keyof typeof TABS;

const isTab = (value: string): value is Tab => value in TABS;

/** Orders, opened inside an initiative: the orders, and the stores' listings. */
export const Route = createFileRoute("/orders")({ component: OrdersRoute });

function OrdersRoute() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // An order's own page sits under the orders tab.
  const tab = useLocation({
    select: (location) => (location.pathname.split("/")[2] === "listings" ? "listings" : "orders"),
  });
  return (
    <HoardPage title={t("orders.title")}>
      <Tabs
        value={tab}
        onValueChange={(value) => {
          if (isTab(value)) void navigate({ to: TABS[value] });
        }}
      >
        <TabsBar aria-label={t("orders.tabs")}>
          {(Object.keys(TABS) as Tab[]).map((name) => (
            <TabsTrigger key={name} value={name}>
              {t(`orders.${name}`)}
            </TabsTrigger>
          ))}
        </TabsBar>
      </Tabs>
      <Outlet />
    </HoardPage>
  );
}
