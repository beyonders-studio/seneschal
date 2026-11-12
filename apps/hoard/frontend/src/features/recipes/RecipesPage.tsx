import { ScrollText } from "lucide-react";
import { useTranslation } from "react-i18next";

import { StatusMessage } from "@/ui/status-message";

/** The recipes list, under Production. */
export function RecipesPage() {
  const { t } = useTranslation("recipes");
  return (
    <StatusMessage
      icon={<ScrollText />}
      title={t("recipes.emptyTitle")}
      description={t("recipes.emptyBody")}
    />
  );
}
