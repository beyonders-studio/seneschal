import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  RecipeCreate,
  RecipeLineRead,
  RecipeRead,
  RecipeWriteOutput,
} from "@/api/generated/hoard.schemas";
import {
  createRecipe,
  getListRecipesQueryKey,
  getReadRecipeQueryKey,
  previewRecipe,
  updateRecipe,
} from "@/api/generated/recipes/recipes";
import { ItemField } from "@/features/adjustments/fields";
import { useMovementLabels } from "@/features/adjustments/labels";
import { orNull, useQuantity, useRatio, useShare } from "@/features/items/format";
import { UnitPicker } from "@/features/items/UnitPicker";
import { IncludeArchived } from "@/features/search/records";
import { useSettingsNames } from "@/features/settings/names";
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
import { useDebouncedValue } from "@/ui/lib/useDebouncedValue";
import { Switch } from "@/ui/switch";
import { Textarea } from "@/ui/textarea";

/** What a new recipe makes: one variation, one base, or one bundle product. */
export type RecipeOutputOf = Pick<RecipeCreate, "variation_id" | "base_id" | "product_id">;

/** A line as it is typed: the item it holds, the name it opened with, the
 *  number written (its amount, or its parts of a ratio), and its unit: its
 *  item's tracking unit while null. */
type DraftLine = {
  key: number;
  itemId: number | null;
  name: string | null;
  written: string;
  unitId: number | null;
};

/** What the switch between amount and ratio changes: how the lines are
 *  written, and a ratio's ingredients total. */
type Draft = {
  byRatio: boolean;
  total: string;
  totalUnitId: number | null;
  lines: DraftLine[];
};

/** Whether `text` is a number above zero, as the API takes one. */
const isPositive = (text: string) => text.trim() !== "" && Number(text) > 0;

/**
 * Adding a recipe for `output`, or editing `recipe`, in a dialog titled
 * `title`: how much a batch makes and how long it takes, and its lines in
 * order, written by amount or by ratio. Hoard works the draft out as it is
 * typed, so each line shows its other column and its share by weight. Its
 * lines are materials, bases and variations of products that are not
 * bundles; a bundle's are variations, and it makes one of itself.
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
  const quantity = useQuantity();
  const ratio = useRatio();
  const share = useShare();
  const { unit } = useSettingsNames();
  const bundle = recipe ? recipe.output === "bundle" : output?.product_id != null;
  const outputOf: RecipeOutputOf = recipe
    ? {
        variation_id: recipe.output === "variation" ? recipe.output_id : null,
        base_id: recipe.output === "base" ? recipe.output_id : null,
        product_id: bundle ? recipe.output_id : null,
      }
    : (output ?? {});
  const [batchQuantity, setBatchQuantity] = useState(recipe?.batch_quantity ?? "1");
  const [minutes, setMinutes] = useState(recipe?.minutes?.toString() ?? "");
  const [notes, setNotes] = useState(recipe?.notes ?? "");
  const [draft, setDraft] = useState<Draft>({
    byRatio: recipe?.by_ratio ?? false,
    total: recipe?.total ?? "",
    totalUnitId: recipe?.total_unit_id ?? null,
    lines: recipe?.lines.map((line, key) => ({
      key,
      itemId: line.item.id,
      name: item(line.item),
      written: line.written,
      unitId: line.unit_id,
    })) ?? [{ key: 0, itemId: null, name: null, written: "", unitId: null }],
  });
  // The draft as it was before the switch was last turned, which turning it
  // back restores until the recipe is saved.
  const [switchedFrom, setSwitchedFrom] = useState<Draft | null>(null);
  const [nextKey, setNextKey] = useState(draft.lines.length);
  const [archived, setArchived] = useState(false);
  const { byRatio, lines } = draft;

  const body = (): RecipeCreate => ({
    ...outputOf,
    batch_quantity: bundle ? "1" : batchQuantity,
    minutes: bundle || !minutes.trim() ? null : Number(minutes),
    by_ratio: byRatio,
    total: byRatio && isPositive(draft.total) ? draft.total : null,
    total_unit_id: byRatio ? draft.totalUnitId : null,
    lines: lines.flatMap((line) =>
      line.itemId === null || !isPositive(line.written)
        ? []
        : [{ item_id: line.itemId, written: line.written, unit_id: line.unitId }]
    ),
  });
  const drafted = useDebouncedValue(bundle || !isPositive(batchQuantity) ? null : body(), 300);
  const preview = useQuery({
    queryKey: ["recipes", "preview", drafted],
    queryFn: ({ signal }) => previewRecipe(drafted as RecipeCreate, undefined, signal),
    enabled: drafted !== null,
    placeholderData: keepPreviousData,
  });
  const worked = new Map<number, RecipeLineRead>(
    preview.isError ? [] : (preview.data?.lines ?? []).map((line) => [line.item.id, line])
  );

  const save = useMutation({
    mutationFn: () => {
      const saved = { ...body(), notes: bundle ? null : orNull(notes) };
      return recipe ? updateRecipe(recipe.id, saved) : createRecipe(saved);
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
    setDraft((current) => ({
      ...current,
      lines: current.lines.map((line) => (line.key === key ? { ...line, ...edit } : line)),
    }));
  const add = () => {
    setDraft((current) => ({
      ...current,
      lines: [
        ...current.lines,
        { key: nextKey, itemId: null, name: null, written: "", unitId: null },
      ],
    }));
    setNextKey(nextKey + 1);
  };
  const remove = (key: number) =>
    setDraft((current) => ({
      ...current,
      lines: current.lines.filter((line) => line.key !== key),
    }));

  /** The draft written the other way, as Hoard worked it out. */
  const fromSwitched = (switched: RecipeWriteOutput): Draft => ({
    byRatio: switched.by_ratio ?? false,
    total: switched.total ?? "",
    totalUnitId: switched.total_unit_id ?? null,
    lines: lines.map((line) => {
      const other = switched.lines.find((each) => each.item_id === line.itemId);
      return other ? { ...line, written: other.written, unitId: other.unit_id ?? null } : line;
    }),
  });
  const turn = (toRatio: boolean) => {
    const switched = preview.data?.switched;
    const next =
      switchedFrom?.byRatio === toRatio
        ? switchedFrom
        : switched && !preview.isError
          ? fromSwitched(switched)
          : { ...draft, byRatio: toRatio };
    setSwitchedFrom(draft);
    setDraft(next);
  };

  const complete =
    lines.every((line) => line.itemId !== null && isPositive(line.written)) &&
    (!byRatio || (isPositive(draft.total) && draft.totalUnitId !== null));

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
          <>
            <Field label={t("dialog.byRatio")} hint={t("dialog.byRatioHint")}>
              <Switch checked={byRatio} onCheckedChange={turn} />
            </Field>
            <div className="grid grid-cols-pair gap-4">
              {byRatio && (
                <Field label={t("dialog.total")} hint={t("dialog.totalHint")}>
                  <div className="flex gap-2">
                    <Input
                      required
                      inputMode="decimal"
                      value={draft.total}
                      onChange={(event) => setDraft({ ...draft, total: event.target.value })}
                    />
                    <div className="w-40 shrink-0">
                      <UnitPicker
                        aria-label={t("dialog.totalUnit")}
                        value={draft.totalUnitId}
                        onChange={(totalUnitId) => setDraft({ ...draft, totalUnitId })}
                      />
                    </div>
                  </div>
                </Field>
              )}
              <Field
                label={t(byRatio ? "dialog.makes" : "dialog.batchQuantity")}
                hint={t(byRatio ? "dialog.makesHint" : "dialog.batchQuantityHint")}
              >
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
            <Field label={t("dialog.notes")} hint={t("dialog.notesHint")}>
              <Textarea value={notes} onChange={(event) => setNotes(event.target.value)} />
            </Field>
          </>
        )}
        <fieldset className="flex flex-col gap-3">
          <legend className="mb-2 text-sm font-medium">
            {bundle ? t("dialog.holds") : t("dialog.lines")}
          </legend>
          <IncludeArchived checked={archived} onCheckedChange={setArchived} />
          {lines.map((line, index) => {
            const figures = line.itemId === null ? undefined : worked.get(line.itemId);
            return (
              <div key={line.key} className="flex flex-wrap items-end gap-2">
                <div className="min-w-48 flex-1">
                  <ItemField
                    kinds={bundle ? ["variation"] : ["material", "base", "variation"]}
                    bundles={bundle}
                    label={t("dialog.item", { number: index + 1 })}
                    value={line.itemId}
                    chosen={line.name}
                    archived={archived}
                    onChange={(itemId) => change(line.key, { itemId, unitId: null })}
                  />
                </div>
                <div className="w-28">
                  <Field label={t(byRatio ? "dialog.parts" : "dialog.quantity")}>
                    <Input
                      required
                      inputMode="decimal"
                      value={line.written}
                      onChange={(event) => change(line.key, { written: event.target.value })}
                    />
                  </Field>
                </div>
                {!bundle && (
                  <>
                    <div className="w-40">
                      <Field label={t("dialog.unit")}>
                        <UnitPicker
                          value={line.unitId ?? figures?.unit_id ?? null}
                          like={figures?.tracking_unit_id}
                          onChange={(unitId) => change(line.key, { unitId })}
                        />
                      </Field>
                    </div>
                    <dl className="flex min-w-24 flex-col pb-2 text-sm">
                      <dt className="text-muted-foreground">
                        {t(byRatio ? "card.amount" : "card.ratio")}
                      </dt>
                      <dd className="tabular-nums">
                        {figures &&
                          (byRatio
                            ? [quantity(figures.amount), unit(figures.unit_id)].join(" ")
                            : ratio(figures.ratio))}
                      </dd>
                    </dl>
                    <dl className="flex min-w-16 flex-col pb-2 text-sm">
                      <dt className="text-muted-foreground">{t("card.share")}</dt>
                      <dd
                        className="tabular-nums"
                        title={figures?.percent === null ? t("card.notWeighed") : undefined}
                      >
                        {figures && (figures.percent === null ? "–" : share(figures.percent))}
                      </dd>
                    </dl>
                  </>
                )}
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={t("dialog.removeLine", { number: index + 1 })}
                  onClick={() => remove(line.key)}
                >
                  <Trash2 aria-hidden />
                </Button>
              </div>
            );
          })}
          <Button type="button" variant="outline" size="sm" className="self-start" onClick={add}>
            <Plus aria-hidden />
            {t("dialog.addLine")}
          </Button>
        </fieldset>
        {preview.isError && (
          <p className="text-sm text-muted-foreground">
            {errorMessage(preview.error, t, "dialog.previewFailed")}
          </p>
        )}
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
