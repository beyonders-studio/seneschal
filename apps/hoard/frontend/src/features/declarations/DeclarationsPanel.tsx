import { useQueryClient } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  getReadDeclarationQueryKey,
  useReadDeclaration,
  useUpdateLotDeclaration,
  useUpdateMaterialDeclaration,
  useUpdateProductDeclaration,
} from "@/api/generated/declarations/declarations";
import { Allergen, type DeclarationRead, FragranceAllergen } from "@/api/generated/hoard.schemas";
import { toast } from "@/components/Toaster";
import { errorMessage } from "@/lib/errors";
import { useSession } from "@/session";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";
import { Checkbox } from "@/ui/checkbox";
import { Combobox } from "@/ui/combobox";
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
import { Label } from "@/ui/label";
import { listFormat, numberFormat } from "@/ui/lib/intl";
import { Switch } from "@/ui/switch";

/** A record that declares what it records of its own. */
type Of = "material" | "product" | "lot";

/** What a material or a product records of its own, each shown as a field
 *  of its page. */
export type Own = "ingredient_name" | "allergens" | "fragrance_allergens";

/** What a material, a product or a lot declares: its allergens, ingredients and
 *  EU fragrance allergens, rolled up through recipes. A material, and a bought
 *  lot, record their own here; a product records its own in the fields of
 *  its page that show them ({@link OwnDeclaration}). */
export function DeclarationsPanel({ of, id }: { of: Of; id: number }) {
  const { t, i18n } = useTranslation("declarations");
  const { read_only } = useSession();
  const [editing, setEditing] = useState(false);
  const declared = useReadDeclaration(of, id).data;
  if (!declared) return null;
  const list = (names: string[]) => listFormat(i18n.resolvedLanguage).format(names);
  const nothing =
    declared.ingredients.length === 0 &&
    declared.allergens.length === 0 &&
    declared.undeclared.length === 0;
  return (
    <div className="flex flex-col gap-3 text-sm">
      {nothing ? (
        <p className="text-muted-foreground">{t("panel.empty")}</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{t("panel.contains")}</span>
            {declared.allergens.length ? (
              declared.allergens.map((allergen) => (
                <Badge key={allergen} variant="secondary">
                  {t(`allergens.${allergen}`)}
                </Badge>
              ))
            ) : (
              <span className="text-muted-foreground">{t("panel.noAllergens")}</span>
            )}
          </div>
          {declared.undeclared.length > 0 && (
            <p className="flex items-start gap-2 text-muted-foreground">
              <TriangleAlert aria-hidden className="size-4 shrink-0 text-warning" />
              {t("panel.undeclared", { names: list(declared.undeclared) })}
            </p>
          )}
          {of !== "lot" && declared.ingredients.length > 0 && (
            <p>
              <span className="font-medium">{t("panel.ingredients")}</span>{" "}
              {list(declared.ingredients)}
            </p>
          )}
          {declared.unweighed.length > 0 && (
            <p className="flex items-start gap-2 text-muted-foreground">
              <TriangleAlert aria-hidden className="size-4 shrink-0 text-warning" />
              {t("panel.unweighed", { names: list(declared.unweighed) })}
            </p>
          )}
          {of === "lot" && declared.records_own && declared.recorded_allergens === null && (
            <p className="text-muted-foreground">{t("panel.fromMaterial")}</p>
          )}
        </>
      )}
      {(declared.fragrance_recorded || declared.recorded_fragrance !== null) && (
        <FragranceDeclaration of={of} declared={declared} />
      )}
      {of !== "product" && declared.records_own && !read_only && (
        <div>
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            {t("panel.edit")}
          </Button>
        </div>
      )}
      {of !== "product" && (
        <Dialog open={editing} onOpenChange={setEditing}>
          <DialogContent>
            {editing && (
              <DeclarationForm of={of} id={id} declared={declared} done={() => setEditing(false)} />
            )}
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}

/**
 * One of what a material or a product records of its own, `own`, as a field
 * of its page: what it records, or that nothing is, and, while Hoard may
 * change, a button that records it. A product declares what it records while
 * it has no recipe, whatever its page shows.
 */
export function OwnDeclaration({
  of,
  id,
  own,
}: {
  of: "material" | "product";
  id: number;
  own: Own;
}) {
  const { t, i18n } = useTranslation("declarations");
  const { read_only } = useSession();
  const [editing, setEditing] = useState(false);
  const declared = useReadDeclaration(of, id).data;
  if (!declared) return null;
  const list = (names: string[]) => listFormat(i18n.resolvedLanguage).format(names);
  const recorded = {
    ingredient_name: declared.ingredient_name,
    allergens:
      declared.recorded_allergens &&
      (declared.recorded_allergens.length
        ? list(declared.recorded_allergens.map((each) => t(`allergens.${each}`)))
        : t("panel.noAllergens")),
    fragrance_allergens:
      declared.recorded_fragrance &&
      (Object.keys(declared.recorded_fragrance).length
        ? list(
            Object.entries(declared.recorded_fragrance).map(([each, share]) =>
              t("own.share", { name: t(`fragrances.${each}`), share })
            )
          )
        : t("fragrance.none")),
  }[own];
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      {recorded ? (
        <span>{recorded}</span>
      ) : (
        <span className="text-muted-foreground">{t("own.unrecorded")}</span>
      )}
      {!read_only && (
        <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
          {t(`own.edits.${own}`)}
        </Button>
      )}
      <Dialog open={editing} onOpenChange={setEditing}>
        <DialogContent>
          {editing && (
            <DeclarationForm of={of} id={id} declared={declared} done={() => setEditing(false)} />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** The EU fragrance allergens past the threshold its label declares them at,
 *  leave-on or rinse-off, and the materials whose fragrance allergens nobody
 *  has recorded. */
function FragranceDeclaration({ of, declared }: { of: Of; declared: DeclarationRead }) {
  const { t, i18n } = useTranslation("declarations");
  const percent = numberFormat(i18n.resolvedLanguage, {
    style: "percent",
    maximumSignificantDigits: 3,
  });
  const share = (value: string | undefined) => percent.format(Number(value ?? 0) / 100);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{t("fragrance.declares")}</span>
        {declared.fragrance_allergens.length ? (
          declared.fragrance_allergens.map((allergen) => (
            <Badge key={allergen} variant="secondary">
              {t("fragrance.share", {
                name: t(`fragrances.${allergen}`),
                share: share(declared.fragrance_shares[allergen]),
              })}
            </Badge>
          ))
        ) : (
          <span className="text-muted-foreground">{t("fragrance.none")}</span>
        )}
      </div>
      <p className="text-muted-foreground">
        {t(declared.rinse_off ? "fragrance.rinseOff" : "fragrance.leaveOn", {
          threshold: share(declared.fragrance_threshold),
        })}
      </p>
      {declared.fragrance_undeclared.length > 0 && (
        <p className="flex items-start gap-2 text-muted-foreground">
          <TriangleAlert aria-hidden className="size-4 shrink-0 text-warning" />
          {t("fragrance.undeclared", {
            names: listFormat(i18n.resolvedLanguage).format(declared.fragrance_undeclared),
          })}
        </p>
      )}
      {of === "lot" && declared.records_own && declared.recorded_fragrance === null && (
        <p className="text-muted-foreground">{t("fragrance.fromMaterial")}</p>
      )}
    </div>
  );
}

/** A material's or a product's ingredient name, allergens and fragrance
 *  allergens, or a bought lot's own. */
function DeclarationForm({
  of,
  id,
  declared,
  done,
}: {
  of: Of;
  id: number;
  declared: DeclarationRead;
  done: () => void;
}) {
  const { t } = useTranslation("declarations");
  const queryClient = useQueryClient();
  const [name, setName] = useState(declared.ingredient_name ?? "");
  const [known, setKnown] = useState(declared.recorded_allergens !== null);
  const [chosen, setChosen] = useState(new Set(declared.recorded_allergens ?? []));
  const [fragranceKnown, setFragranceKnown] = useState(declared.recorded_fragrance !== null);
  const [shares, setShares] = useState<Partial<Record<FragranceAllergen, string>>>(
    declared.recorded_fragrance ?? {}
  );
  const fragrances = Object.values(FragranceAllergen).filter((each) => each in shares);
  const saved = {
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: getReadDeclarationQueryKey(of, id) });
      toast.success(t("panel.saved"));
      done();
    },
    onError: (error: unknown) => toast.error(errorMessage(error, t, "panel.saveFailed")),
  };
  const material = useUpdateMaterialDeclaration({ mutation: saved });
  const product = useUpdateProductDeclaration({ mutation: saved });
  const lot = useUpdateLotDeclaration({ mutation: saved });
  const allergens = known ? Object.values(Allergen).filter((each) => chosen.has(each)) : null;
  const fragrance_allergens = fragranceKnown ? shares : null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const data = { ingredient_name: name.trim() || null, allergens, fragrance_allergens };
    if (of === "material") {
      material.mutate({ materialId: id, data });
    } else if (of === "product") {
      product.mutate({ productId: id, data });
    } else {
      lot.mutate({ lotId: id, data: { allergens, fragrance_allergens } });
    }
  };
  const pick = (keys: string[]) =>
    setShares(
      Object.fromEntries(
        Object.values(FragranceAllergen)
          .filter((each) => keys.includes(each))
          .map((each) => [each, shares[each] ?? ""])
      )
    );
  const toggle = (allergen: Allergen, on: boolean) => {
    const next = new Set(chosen);
    if (on) next.add(allergen);
    else next.delete(allergen);
    setChosen(next);
  };
  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>{t("panel.title")}</DialogTitle>
      </DialogHeader>
      <DialogBody className="flex flex-col gap-4">
        {of === "product" && <p className="text-sm text-muted-foreground">{t("own.hint")}</p>}
        {of !== "lot" && (
          <Field label={t("form.ingredientName")} hint={t("form.ingredientNameHint")}>
            <Input value={name} maxLength={200} onChange={(event) => setName(event.target.value)} />
          </Field>
        )}
        <div className="flex items-center gap-2">
          <Switch id="declares" checked={known} onCheckedChange={setKnown} />
          <Label htmlFor="declares">{of === "lot" ? t("form.lotKnown") : t("form.known")}</Label>
        </div>
        {known && (
          <fieldset className="grid grid-cols-fill-40/3 gap-2">
            <legend className="mb-2 text-sm font-medium">{t("form.allergens")}</legend>
            {Object.values(Allergen).map((allergen) => (
              <div key={allergen} className="flex items-center gap-2">
                <Checkbox
                  id={`allergen-${allergen}`}
                  checked={chosen.has(allergen)}
                  onCheckedChange={(on) => toggle(allergen, on === true)}
                />
                <Label htmlFor={`allergen-${allergen}`}>{t(`allergens.${allergen}`)}</Label>
              </div>
            ))}
          </fieldset>
        )}
        <div className="flex items-center gap-2">
          <Switch
            id="declares-fragrance"
            checked={fragranceKnown}
            onCheckedChange={setFragranceKnown}
          />
          <Label htmlFor="declares-fragrance">
            {of === "lot" ? t("form.lotFragranceKnown") : t("form.fragranceKnown")}
          </Label>
        </div>
        {fragranceKnown && (
          <>
            <Field label={t("form.fragrances")} hint={t("form.fragrancesHint")}>
              <Combobox
                multiple
                items={Object.values(FragranceAllergen).map((each) => ({
                  value: each,
                  label: t(`fragrances.${each}`),
                }))}
                value={fragrances}
                onValueChange={pick}
                placeholder={t("form.fragrancesChoose")}
                searchPlaceholder={t("form.fragrancesSearch")}
                emptyMessage={t("form.fragrancesNone")}
              />
            </Field>
            {fragrances.length > 0 && (
              <div className="grid grid-cols-pair gap-4">
                {fragrances.map((each) => (
                  <Field key={each} label={t("form.share", { name: t(`fragrances.${each}`) })}>
                    <Input
                      inputMode="decimal"
                      required
                      value={shares[each] ?? ""}
                      onChange={(event) => setShares({ ...shares, [each]: event.target.value })}
                    />
                  </Field>
                ))}
              </div>
            )}
          </>
        )}
      </DialogBody>
      <DialogFooter
        submitLabel={t("hoard:common.save")}
        pending={material.isPending || product.isPending || lot.isPending}
        pendingLabel={t("hoard:common.saving")}
      />
    </form>
  );
}
