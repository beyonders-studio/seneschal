import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import type { ItemBin } from "@/api/generated/hoard.schemas";
import { setItemBin } from "@/api/generated/items/items";
import { useListSettingsLocations } from "@/api/generated/settings/settings";
import { LocationField } from "@/features/adjustments/fields";
import { orNull } from "@/features/items/format";
import { useSettingsNames } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
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
import { listFormat } from "@/ui/lib/intl";

/** A bin with none of its parts. */
export const NO_BIN: ItemBin = { zone: null, section: null, position: null };

/** The parts of a bin, in the order they narrow it down. */
const PARTS = ["zone", "section", "position"] as const;

/** A bin as typed, its parts trimmed and those left blank null. */
export const trimmedBin = (bin: ItemBin): ItemBin =>
  Object.fromEntries(PARTS.map((part) => [part, orNull(bin[part] ?? "")]));

/** A bin as one line: its parts that are set, listed. */
export function useBinLabel(): (bin: ItemBin | null) => string {
  const { i18n } = useTranslation();
  const list = listFormat(i18n.resolvedLanguage, { type: "unit", style: "short" });
  return (bin) => list.format(PARTS.flatMap((part) => bin?.[part] ?? []));
}

/** A bin's zone, section and position, each of which may be left blank. */
export function BinFields({
  value,
  onChange,
}: {
  value: ItemBin;
  onChange: (bin: ItemBin) => void;
}) {
  const { t } = useTranslation("catalog");
  return (
    <div className="grid grid-cols-fill-24/3 gap-4">
      {PARTS.map((part) => (
        <Field key={part} label={t(`items.bin.${part}`)}>
          <Input
            value={value[part] ?? ""}
            onChange={(event) => onChange({ ...value, [part]: event.target.value })}
          />
        </Field>
      ))}
    </div>
  );
}

/**
 * Setting where an item sits at a location, in a dialog: at `location`, or at
 * one picked here. Leaving every part blank removes the bin.
 */
export function BinDialog({
  itemId,
  location,
  bin,
  open,
  onOpenChange,
  onSaved,
}: {
  itemId: number;
  location: number | null;
  bin: ItemBin | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {open && (
          <BinForm
            itemId={itemId}
            location={location}
            bin={bin}
            onSaved={() => {
              onOpenChange(false);
              onSaved();
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function BinForm({
  itemId,
  location,
  bin,
  onSaved,
}: {
  itemId: number;
  location: number | null;
  bin: ItemBin | null;
  onSaved: () => void;
}) {
  const { t } = useTranslation("catalog");
  const names = useSettingsNames();
  const [at, setAt] = useState(location);
  const [value, setValue] = useState(bin ?? NO_BIN);
  const main = useListSettingsLocations().data?.find((place) => place.main)?.id ?? null;
  const save = useMutation({
    mutationFn: (locationId: number) => setItemBin(itemId, locationId, trimmedBin(value)),
    onSuccess: onSaved,
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const locationId = at ?? main;
    if (locationId !== null) save.mutate(locationId);
  };
  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>
          {location === null
            ? t("items.bin.add")
            : t("items.bin.edit", { location: names.location(location) })}
        </DialogTitle>
      </DialogHeader>
      <DialogBody className="flex flex-col gap-4">
        {location === null && <LocationField value={at} onChange={setAt} />}
        <BinFields value={value} onChange={setValue} />
        <p className="text-sm text-muted-foreground">{t("items.bin.hint")}</p>
        {save.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(save.error, t, "items.bin.saveFailed")}
          </p>
        )}
      </DialogBody>
      <DialogFooter
        submitLabel={t("hoard:common.save")}
        pending={save.isPending}
        pendingLabel={t("hoard:common.saving")}
      />
    </form>
  );
}
