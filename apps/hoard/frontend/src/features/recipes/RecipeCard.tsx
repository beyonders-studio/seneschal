import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Pencil, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { RecipeRead } from "@/api/generated/hoard.schemas";
import {
  deleteRecipe,
  getListRecipesQueryKey,
  useReadRecipe,
} from "@/api/generated/recipes/recipes";
import { toast } from "@/components/Toaster";
import { useMovementLabels } from "@/features/adjustments/labels";
import { useQuantity } from "@/features/items/format";
import { RecipeDialog } from "@/features/recipes/RecipeDialog";
import { useSettingsNames } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
import { useSession } from "@/session";
import { Button } from "@/ui/button";
import { ConfirmDialog } from "@/ui/confirm-dialog";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";
import { numberFormat } from "@/ui/lib/intl";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** Whether `text` is a quantity a recipe scales to: a number above zero. */
const isQuantity = (text: string) => text.trim() !== "" && Number(text) > 0;

/**
 * A recipe, under `heading` when it has one: what a batch makes and takes,
 * its lines with each one's share by weight, and, but for a bundle, a quantity
 * to scale it to. While Hoard may change, it is edited and deleted here.
 */
export function RecipeCard({ recipe, heading }: { recipe: RecipeRead; heading?: string }) {
  const { t, i18n } = useTranslation("recipes");
  const queryClient = useQueryClient();
  const { read_only } = useSession();
  const { unit } = useSettingsNames();
  const { item } = useMovementLabels();
  const quantity = useQuantity();
  const [scaleTo, setScaleTo] = useState("");
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const bundle = recipe.output === "bundle";
  const scaled = useReadRecipe(
    recipe.id,
    { quantity: scaleTo },
    { query: { enabled: !bundle && isQuantity(scaleTo) } }
  ).data;
  const shown = isQuantity(scaleTo) && scaled ? scaled : recipe;
  const percent = numberFormat(i18n.resolvedLanguage, {
    style: "percent",
    maximumFractionDigits: 2,
  });
  const removal = useMutation({
    mutationFn: () => deleteRecipe(recipe.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: getListRecipesQueryKey() }),
    onError: (error) => toast.error(errorMessage(error, t, "card.removeFailed")),
  });
  const name = item(recipe);
  const withUnit = (amount: string, unitId: number | null) =>
    [quantity(amount), unitId === null ? "" : unit(unitId)].filter(Boolean).join(" ");

  return (
    <section className="flex flex-col gap-3 rounded-md border p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          {heading && <h4 className="text-sm font-semibold">{heading}</h4>}
          {!bundle && (
            <p className="text-sm text-muted-foreground">
              {[
                t("card.makes", { quantity: withUnit(shown.batch_quantity, recipe.unit_id) }),
                recipe.minutes !== null && t("card.minutes", { count: recipe.minutes }),
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          )}
        </div>
        {!read_only && (
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
              <Pencil aria-hidden />
              {t("hoard:common.edit")}
            </Button>
            <Button variant="outline" size="sm" onClick={() => setRemoving(true)}>
              <Trash2 aria-hidden />
              {t("hoard:common.delete")}
            </Button>
          </div>
        )}
      </div>
      {!bundle && (
        <div className="w-40">
          <Field label={t("card.scaleTo")}>
            <Input
              inputMode="decimal"
              value={scaleTo}
              placeholder={quantity(recipe.batch_quantity)}
              onChange={(event) => setScaleTo(event.target.value)}
            />
          </Field>
        </div>
      )}
      {shown.lines.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("card.noLines")}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("card.item")}</TableHead>
              <TableHead className="text-end">{t("hoard:common.quantity")}</TableHead>
              {!bundle && <TableHead className="text-end">{t("card.share")}</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.lines.map((line) => (
              <TableRow key={line.item.id}>
                <TableCell>{item(line.item)}</TableCell>
                <TableCell className="text-end tabular-nums">
                  {withUnit(line.quantity, line.unit_id)}
                </TableCell>
                {!bundle && (
                  <TableCell className="text-end tabular-nums">
                    {line.percent !== null && percent.format(Number(line.percent) / 100)}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <RecipeDialog
        recipe={recipe}
        title={t(bundle ? "dialog.bundleTitle" : "dialog.addTitle", { name })}
        open={editing}
        onOpenChange={setEditing}
      />
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title={t(bundle ? "card.removeBundleTitle" : "card.removeTitle", { name })}
        description={t("card.removeBody")}
        confirmLabel={t("hoard:common.delete")}
        destructive
        isLoading={removal.isPending}
        onConfirm={() => removal.mutate()}
      />
    </section>
  );
}
