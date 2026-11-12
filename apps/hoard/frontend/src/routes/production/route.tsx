import { createFileRoute, Outlet, useLocation, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { HoardPage } from "@/components/HoardPage";
import { Tabs, TabsBar, TabsTrigger } from "@/ui/tabs";

/** Each of Production's tabs, and the page it opens. */
const TABS = {
  runs: "/production/runs",
  recipes: "/production/recipes",
} as const;

type Tab = keyof typeof TABS;

const isTab = (value: string): value is Tab => value in TABS;

/** Production, opened inside an initiative: its runs, and the recipes they make. */
export const Route = createFileRoute("/production")({ component: ProductionPage });

function ProductionPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const tab = useLocation({ select: (location) => location.pathname.split("/")[2] ?? "" });
  return (
    <HoardPage title={t("production.title")}>
      <Tabs
        value={tab}
        onValueChange={(value) => {
          if (isTab(value)) void navigate({ to: TABS[value] });
        }}
      >
        <TabsBar aria-label={t("production.tabs")}>
          {(Object.keys(TABS) as Tab[]).map((name) => (
            <TabsTrigger key={name} value={name}>
              {t(`production.${name}`)}
            </TabsTrigger>
          ))}
        </TabsBar>
      </Tabs>
      <Outlet />
    </HoardPage>
  );
}
