import { useQueryClient } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  getReadDeclarationQueryKey,
  useReadDeclaration,
  useUpdateLotDeclaration,
  useUpdateMaterialDeclaration,
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

/** What a material, a product or a lot declares: its allergens, ingredients and
 *  EU fragrance allergens, rolled up through recipes. A material, and a bought
 *  lot, record their own. */
export function DeclarationsPanel({ of, id }: { of: "material" | "product" | "lot"; id: number }) {
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

/** The EU fragrance allergens past the threshold its label declares them at,
 *  leave-on or rinse-off, and the materials whose fragrance allergens nobody
 *  has recorded. */
function FragranceDeclaration({
  of,
  declared,
}: {
  of: "material" | "product" | "lot";
  declared: DeclarationRead;
}) {
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

/** A material's ingredient name, allergens and fragrance allergens, or a
 *  bought lot's own. */
function DeclarationForm({
  of,
  id,
  declared,
  done,
}: {
  of: "material" | "lot";
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
  const lot = useUpdateLotDeclaration({ mutation: saved });
  const allergens = known ? Object.values(Allergen).filter((each) => chosen.has(each)) : null;
  const fragrance_allergens = fragranceKnown ? shares : null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (of === "material") {
      material.mutate({
        materialId: id,
        data: { ingredient_name: name.trim() || null, allergens, fragrance_allergens },
      });
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
        {of === "material" && (
          <Field label={t("form.ingredientName")} hint={t("form.ingredientNameHint")}>
            <Input value={name} maxLength={200} onChange={(event) => setName(event.target.value)} />
          </Field>
        )}
        <div className="flex items-center gap-2">
          <Switch id="declares" checked={known} onCheckedChange={setKnown} />
          <Label htmlFor="declares">
            {of === "material" ? t("form.known") : t("form.lotKnown")}
          </Label>
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
            {of === "material" ? t("form.fragranceKnown") : t("form.lotFragranceKnown")}
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
        pending={material.isPending || lot.isPending}
        pendingLabel={t("hoard:common.saving")}
      />
    </form>
  );
}
