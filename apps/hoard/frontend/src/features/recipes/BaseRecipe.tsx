import { Plus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { BaseRead } from "@/api/generated/hoard.schemas";
import { useListRecipes } from "@/api/generated/recipes/recipes";
import { RecipeCard } from "@/features/recipes/RecipeCard";
import { RecipeDialog } from "@/features/recipes/RecipeDialog";
import { useSession } from "@/session";
import { Button } from "@/ui/button";

/** A base's recipe on its page, or, until it has one, a way to add it. */
export function BaseRecipe({ base }: { base: BaseRead }) {
  const { t } = useTranslation("recipes");
  const { read_only } = useSession();
  const [adding, setAdding] = useState(false);
  const recipes = useListRecipes({ base_id: base.id });
  const recipe = recipes.data?.items[0];

  if (!recipes.data) return null;
  if (recipe) return <RecipeCard recipe={recipe} />;
  return (
    <div className="flex flex-col items-start gap-2">
      <p className="text-sm text-muted-foreground">{t("section.empty")}</p>
      {!read_only && (
        <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
          <Plus aria-hidden />
          {t("section.add")}
        </Button>
      )}
      <RecipeDialog
        output={{ base_id: base.id }}
        title={t("dialog.addTitle", { name: base.name })}
        open={adding}
        onOpenChange={setAdding}
      />
    </div>
  );
}
