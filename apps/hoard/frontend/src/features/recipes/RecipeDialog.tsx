import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import type { RecipeCreate, RecipeRead } from "@/api/generated/hoard.schemas";
import {
  createRecipe,
  getListRecipesQueryKey,
  getReadRecipeQueryKey,
  updateRecipe,
} from "@/api/generated/recipes/recipes";
import { ItemField } from "@/features/adjustments/fields";
import { useMovementLabels } from "@/features/adjustments/labels";
import { errorMessage } from "@/lib/errors";
import { Button } from "@/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/ui/dialog";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";

/** What a new recipe makes: one variation, one base, or one bundle product. */
export type RecipeOutputOf = Pick<RecipeCreate, "variation_id" | "base_id" | "product_id">;

/** A line as it is typed: the item it holds, the name it opened with, and how much. */
type DraftLine = { key: number; itemId: number | null; name: string | null; quantity: string };

/**
 * Adding a recipe for `output`, or editing `recipe`, in a dialog titled
 * `title`: how much a batch makes and how long it takes, and its lines in
 * order. A bundle's lines are variations, and it makes one of itself.
 */
export function RecipeDialog({
  recipe,
  output,
  title,
  open,
  onOpenChange,
}: {
  recipe?: RecipeRead;
  output?: RecipeOutputOf;
  title: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {open && (
          <RecipeForm
            recipe={recipe}
            output={output}
            title={title}
            onSaved={() => onOpenChange(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function RecipeForm({
  recipe,
  output,
  title,
  onSaved,
}: {
  recipe?: RecipeRead;
  output?: RecipeOutputOf;
  title: string;
  onSaved: () => void;
}) {
  const { t } = useTranslation("recipes");
  const queryClient = useQueryClient();
  const { item } = useMovementLabels();
  const bundle = recipe ? recipe.output === "bundle" : output?.product_id != null;
  const [batchQuantity, setBatchQuantity] = useState(recipe?.batch_quantity ?? "1");
  const [minutes, setMinutes] = useState(recipe?.minutes?.toString() ?? "");
  const [lines, setLines] = useState<DraftLine[]>(
    recipe?.lines.map((line, key) => ({
      key,
      itemId: line.item.id,
      name: item(line.item),
      quantity: line.quantity,
    })) ?? [{ key: 0, itemId: null, name: null, quantity: "" }]
  );
  const [nextKey, setNextKey] = useState(lines.length);
  const save = useMutation({
    mutationFn: () => {
      const body = {
        batch_quantity: bundle ? "1" : batchQuantity,
        minutes: bundle || !minutes.trim() ? null : Number(minutes),
        lines: lines.flatMap((line) =>
          line.itemId === null ? [] : [{ item_id: line.itemId, quantity: line.quantity }]
        ),
      };
      return recipe ? updateRecipe(recipe.id, body) : createRecipe({ ...body, ...output });
    },
    onSuccess: async (saved) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: getListRecipesQueryKey() }),
        queryClient.invalidateQueries({ queryKey: getReadRecipeQueryKey(saved.id) }),
      ]);
      onSaved();
    },
  });

  const change = (key: number, edit: Partial<DraftLine>) =>
    setLines((current) => current.map((line) => (line.key === key ? { ...line, ...edit } : line)));
  const add = () => {
    setLines((current) => [...current, { key: nextKey, itemId: null, name: null, quantity: "" }]);
    setNextKey(nextKey + 1);
  };
  const complete = lines.every((line) => line.itemId !== null && line.quantity.trim());

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (complete) save.mutate();
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
      </DialogHeader>
      <DialogBody className="flex flex-col gap-4">
        {!bundle && (
          <div className="grid grid-cols-pair gap-4">
            <Field label={t("dialog.batchQuantity")} hint={t("dialog.batchQuantityHint")}>
              <Input
                required
                inputMode="decimal"
                value={batchQuantity}
                onChange={(event) => setBatchQuantity(event.target.value)}
              />
            </Field>
            <Field label={t("dialog.minutes")}>
              <Input
                inputMode="numeric"
                value={minutes}
                onChange={(event) => setMinutes(event.target.value)}
              />
            </Field>
          </div>
        )}
        <fieldset className="flex flex-col gap-3">
          <legend className="mb-2 text-sm font-medium">
            {bundle ? t("dialog.holds") : t("dialog.lines")}
          </legend>
          {lines.map((line, index) => (
            <div key={line.key} className="flex flex-wrap items-end gap-2">
              <div className="min-w-48 flex-1">
                <ItemField
                  kinds={bundle ? ["variation"] : ["material", "base"]}
                  label={t("dialog.item", { number: index + 1 })}
                  value={line.itemId}
                  chosen={line.name}
                  onChange={(itemId) => change(line.key, { itemId })}
                />
              </div>
              <div className="w-32">
                <Field label={t("dialog.quantity")}>
                  <Input
                    required
                    inputMode="decimal"
                    value={line.quantity}
                    onChange={(event) => change(line.key, { quantity: event.target.value })}
                  />
                </Field>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={t("dialog.removeLine", { number: index + 1 })}
                onClick={() => setLines((current) => current.filter((each) => each !== line))}
              >
                <Trash2 aria-hidden />
              </Button>
            </div>
          ))}
          <Button type="button" variant="outline" size="sm" className="self-start" onClick={add}>
            <Plus aria-hidden />
            {t("dialog.addLine")}
          </Button>
        </fieldset>
        {save.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(save.error, t, "dialog.saveFailed")}
          </p>
        )}
      </DialogBody>
      <DialogFooter
        submitLabel={t("hoard:common.save")}
        pending={save.isPending}
        pendingLabel={t("hoard:common.saving")}
        disabled={!complete || (!bundle && !batchQuantity.trim())}
      />
    </form>
  );
}
