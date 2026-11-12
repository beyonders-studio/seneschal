import { Boxes, Plus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { ProductDetail } from "@/api/generated/hoard.schemas";
import { useListRecipes } from "@/api/generated/recipes/recipes";
import { useVariationName } from "@/features/products/variations";
import { RecipeCard } from "@/features/recipes/RecipeCard";
import { RecipeDialog, type RecipeOutputOf } from "@/features/recipes/RecipeDialog";
import { useSession } from "@/session";
import { Button } from "@/ui/button";

/** The most variations a product's page shows the recipes of. */
const MOST_RECIPES = 200;

/**
 * A product's recipes on its page: each variation's, or, for a bundle, the
 * variations it holds. A product whose variations have no recipe yet can be
 * made a bundle instead.
 */
export function RecipeSection({ product }: { product: ProductDetail }) {
  const { t } = useTranslation("recipes");
  const { read_only } = useSession();
  const variationName = useVariationName();
  const [adding, setAdding] = useState<{ output: RecipeOutputOf; title: string } | null>(null);
  const recipes = useListRecipes({ product_id: product.id, limit: MOST_RECIPES }).data?.items;
  if (!recipes) return null;

  const bundle = recipes.find((recipe) => recipe.output === "bundle");
  const several = product.variations.length > 1;
  const headingOf = (name: string) => (several ? t("section.of", { variation: name }) : undefined);

  return (
    <div className="flex flex-col gap-3">
      {bundle ? (
        <RecipeCard recipe={bundle} heading={t("section.bundle")} />
      ) : (
        product.variations.map((variation) => {
          const name = variationName(variation);
          const recipe = recipes.find(
            (each) => each.output === "variation" && each.output_id === variation.id
          );
          if (recipe)
            return <RecipeCard key={variation.id} recipe={recipe} heading={headingOf(name)} />;
          return (
            <div key={variation.id} className="flex flex-wrap items-center gap-2">
              <p className="text-sm text-muted-foreground">
                {several ? t("section.emptyOf", { variation: name }) : t("section.empty")}
              </p>
              {!read_only && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    setAdding({
                      output: { variation_id: variation.id },
                      title: several
                        ? t("dialog.addTitleOf", { name: product.name, variation: name })
                        : t("dialog.addTitle", { name: product.name }),
                    })
                  }
                >
                  <Plus aria-hidden />
                  {t("section.add")}
                </Button>
              )}
            </div>
          );
        })
      )}
      {!bundle && recipes.length === 0 && !read_only && (
        <div className="flex flex-col items-start gap-1">
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              setAdding({
                output: { product_id: product.id },
                title: t("dialog.bundleTitle", { name: product.name }),
              })
            }
          >
            <Boxes aria-hidden />
            {t("section.makeBundle")}
          </Button>
          <p className="text-xs text-muted-foreground">{t("section.bundleHint")}</p>
        </div>
      )}
      <RecipeDialog
        output={adding?.output}
        title={adding?.title ?? ""}
        open={adding !== null}
        onOpenChange={(open) => {
          if (!open) setAdding(null);
        }}
      />
    </div>
  );
}
