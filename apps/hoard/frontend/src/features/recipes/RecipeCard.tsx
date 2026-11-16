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
import { useQuantity, useRatio, useShare } from "@/features/items/format";
import { IdSelect } from "@/features/items/IdSelect";
import { StatusBadge } from "@/features/items/ItemList";
import { RecipeDialog } from "@/features/recipes/RecipeDialog";
import { useSettingsNames } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
import { useSession } from "@/session";
import { Button } from "@/ui/button";
import { ConfirmDialog } from "@/ui/confirm-dialog";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** Whether `text` is a quantity a recipe scales to: a number above zero. */
const isQuantity = (text: string) => text.trim() !== "" && Number(text) > 0;

/**
 * A recipe, under `heading` when it has one: what a batch makes and takes,
 * its lines with each one's ratio, amount in the unit it was written in and
 * share by weight, and, but for a bundle, a quantity to scale it to: of what
 * it makes, or of one of its lines. While Hoard may change, it is edited and
 * deleted here.
 */
export function RecipeCard({ recipe, heading }: { recipe: RecipeRead; heading?: string }) {
  const { t } = useTranslation("recipes");
  const queryClient = useQueryClient();
  const { read_only } = useSession();
  const { unit } = useSettingsNames();
  const { item } = useMovementLabels();
  const quantity = useQuantity();
  const ratio = useRatio();
  const share = useShare();
  const [scaleTo, setScaleTo] = useState("");
  // The line whose amount it scales to; null for what it makes.
  const [scaleBy, setScaleBy] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const bundle = recipe.output === "bundle";
  const by = recipe.lines.find((line) => line.item.id === scaleBy);
  const scaled = useReadRecipe(
    recipe.id,
    by
      ? { ingredient_id: by.item.id, amount: scaleTo, unit_id: by.unit_id }
      : { quantity: scaleTo },
    { query: { enabled: !bundle && isQuantity(scaleTo) } }
  ).data;
  const shown = isQuantity(scaleTo) && scaled ? scaled : recipe;
  const removal = useMutation({
    mutationFn: () => deleteRecipe(recipe.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: getListRecipesQueryKey() }),
    onError: (error) => toast.error(errorMessage(error, t, "card.removeFailed")),
  });
  const name = item(recipe);
  const withUnit = (amount: string, unitId: number | null) =>
    [quantity(amount), unitId === null ? "" : unit(unitId)].filter(Boolean).join(" ");
  const facts = [
    t("card.makes", { quantity: withUnit(shown.batch_quantity, recipe.unit_id) }),
    shown.total !== null &&
      t("card.total", { quantity: withUnit(shown.total, shown.total_unit_id) }),
    recipe.minutes !== null && t("card.minutes", { count: recipe.minutes }),
  ];

  return (
    <section className="flex flex-col gap-3 rounded-md border p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          {heading && <h4 className="text-sm font-semibold">{heading}</h4>}
          {!bundle && (
            <p className="text-sm text-muted-foreground">{facts.filter(Boolean).join(" · ")}</p>
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
        <div className="flex flex-wrap items-end gap-2">
          <div className="w-56">
            <Field label={t("card.scaleBy")}>
              <IdSelect
                value={scaleBy}
                options={recipe.lines.map((line) => ({ id: line.item.id, name: item(line.item) }))}
                onChange={setScaleBy}
                none={t("card.scaleByOutput")}
              />
            </Field>
          </div>
          <div className="w-40">
            <Field label={t("card.scaleTo")}>
              <Input
                inputMode="decimal"
                value={scaleTo}
                placeholder={quantity(by ? by.amount : recipe.batch_quantity)}
                onChange={(event) => setScaleTo(event.target.value)}
              />
            </Field>
          </div>
          <span className="pb-2 text-sm text-muted-foreground">
            {by ? unit(by.unit_id) : recipe.unit_id !== null && unit(recipe.unit_id)}
          </span>
        </div>
      )}
      {recipe.notes && <p className="text-sm whitespace-pre-line">{recipe.notes}</p>}
      {shown.lines.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("card.noLines")}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("card.item")}</TableHead>
              {!bundle && <TableHead className="text-end">{t("card.ratio")}</TableHead>}
              <TableHead className="text-end">{t("card.amount")}</TableHead>
              {!bundle && <TableHead className="text-end">{t("card.share")}</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.lines.map((line) => (
              <TableRow key={line.item.id}>
                <TableCell>
                  <span className="inline-flex items-center gap-2">
                    {item(line.item)}
                    <StatusBadge status={line.item.archived ? "archived" : "active"} />
                  </span>
                </TableCell>
                {!bundle && (
                  <TableCell className="text-end tabular-nums">{ratio(line.ratio)}</TableCell>
                )}
                <TableCell className="text-end tabular-nums">
                  {withUnit(line.amount, line.unit_id)}
                </TableCell>
                {!bundle && (
                  <TableCell
                    className="text-end tabular-nums"
                    title={line.percent === null ? t("card.notWeighed") : undefined}
                  >
                    {line.percent === null ? "–" : share(line.percent)}
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
