import { Lock } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { IndustryPicker } from "@/features/setups/IndustryPicker";
import { useSession } from "@/session";
import { Alert, AlertDescription, AlertTitle } from "@/ui/alert";

/** One of Hoard's pages: its title, a notice while Hoard is read-only, the
 *  question of which kind of business this is the first time Hoard is opened,
 *  and what the page holds. */
export function HoardPage({ title, children }: { title: string; children: ReactNode }) {
  const { t } = useTranslation();
  const { read_only } = useSession();
  return (
    <div className="flex min-h-full flex-col gap-4 p-4 sm:p-6">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {read_only && (
        <Alert>
          <Lock aria-hidden />
          <AlertTitle>{t("readOnly.title")}</AlertTitle>
          <AlertDescription>{t("readOnly.body")}</AlertDescription>
        </Alert>
      )}
      <IndustryPicker />
      {children}
    </div>
  );
}
