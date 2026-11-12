import { Link } from "@tanstack/react-router";
import { ScrollText } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { RecipeRead } from "@/api/generated/hoard.schemas";
import { useListRecipes } from "@/api/generated/recipes/recipes";
import { useMovementLabels } from "@/features/adjustments/labels";
import { useQuantity } from "@/features/items/format";
import { ItemList, useItemSearch } from "@/features/items/ItemList";
import { useSettingsNames } from "@/features/settings/names";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** What a recipe makes, as a link to its page. */
function Output({ recipe }: { recipe: RecipeRead }) {
  const { item } = useMovementLabels();
  const name = item(recipe);
  if (recipe.output === "base")
    return (
      <Link
        to="/stock/bases/$baseId"
        params={{ baseId: String(recipe.output_id) }}
        className="hover:underline"
      >
        {name}
      </Link>
    );
  return (
    <Link
      to="/stock/products/$productId"
      params={{ productId: String(recipe.product_id) }}
      className="hover:underline"
    >
      {name}
    </Link>
  );
}

/** The recipes list, under Production: what each makes, and how much a batch
 *  makes of it. Recipes are added on a base's or a product's page. */
export function RecipesPage() {
  const { t } = useTranslation("recipes");
  const search = useItemSearch();
  const recipes = useListRecipes(search.params);
  const quantity = useQuantity();
  const { unit } = useSettingsNames();

  return (
    <ItemList
      search={search}
      total={recipes.data?.total}
      empty={{
        icon: <ScrollText />,
        title: t("recipes.emptyTitle"),
        description: t("recipes.emptyBody"),
      }}
      failed={recipes.isError}
      onRetry={() => void recipes.refetch()}
    >
      {recipes.data?.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("catalog:items.noMatch")}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("recipes.output")}</TableHead>
              <TableHead>{t("recipes.kind")}</TableHead>
              <TableHead className="text-end">{t("recipes.batch")}</TableHead>
              <TableHead className="text-end">{t("recipes.lines")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {recipes.data?.items.map((recipe) => (
              <TableRow key={recipe.id}>
                <TableCell>
                  <Output recipe={recipe} />
                </TableCell>
                <TableCell>{t(`recipes.kinds.${recipe.output}`)}</TableCell>
                <TableCell className="text-end tabular-nums">
                  {recipe.output !== "bundle" &&
                    [quantity(recipe.batch_quantity), recipe.unit_id && unit(recipe.unit_id)]
                      .filter(Boolean)
                      .join(" ")}
                </TableCell>
                <TableCell className="text-end tabular-nums">{recipe.lines.length}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </ItemList>
  );
}
