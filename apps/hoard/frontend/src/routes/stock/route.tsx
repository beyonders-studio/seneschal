import { createFileRoute, Outlet, useLocation, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { HoardPage } from "@/components/HoardPage";
import { Tabs, TabsBar, TabsTrigger } from "@/ui/tabs";

/** Each of Stock's tabs, and the page it opens. */
const TABS = {
  materials: "/stock/materials",
  bases: "/stock/bases",
  products: "/stock/products",
  purchases: "/stock/purchases",
  adjustments: "/stock/adjustments",
  stocktakes: "/stock/stocktakes",
} as const;

type Tab = keyof typeof TABS;

const isTab = (value: string): value is Tab => value in TABS;

/** Stock, opened inside an initiative: a tab for each kind of record. */
export const Route = createFileRoute("/stock")({ component: StockPage });

function StockPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const tab = useLocation({ select: (location) => location.pathname.split("/")[2] ?? "" });
  return (
    <HoardPage title={t("stock.title")}>
      <Tabs
        value={tab}
        onValueChange={(value) => {
          if (isTab(value)) void navigate({ to: TABS[value] });
        }}
      >
        <TabsBar aria-label={t("stock.tabs")}>
          {(Object.keys(TABS) as Tab[]).map((name) => (
            <TabsTrigger key={name} value={name}>
              {t(`stock.${name}`)}
            </TabsTrigger>
          ))}
        </TabsBar>
      </Tabs>
      <Outlet />
    </HoardPage>
  );
}
