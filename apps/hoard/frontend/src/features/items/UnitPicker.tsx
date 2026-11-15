import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { SettingsUnitRead } from "@/api/generated/hoard.schemas";
import {
  createSettingsUnit,
  getListSettingsUnitsQueryKey,
  useListSettingsUnits,
} from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { useChangesSettings } from "@/features/settings/NamedList";
import { errorMessage } from "@/lib/errors";
import { Combobox, type ComboboxItem } from "@/ui/combobox";

/** The value that stands for no unit, which a combobox item cannot carry as empty. */
const NONE = "none";

/** A name as units are searched by: in lower case, without full stops, an
 *  underscore read as a space, and single spaces, as Hoard matches them. */
const normalized = (name: string) =>
  name
    .toLowerCase()
    .replaceAll(".", "")
    .replaceAll("_", " ")
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");

/** Every name a unit is found by: its names in the reader's words, its own
 *  name and, for a standard unit, its aliases. */
function useFoundBy(): (unit: SettingsUnitRead) => string[] {
  const { t } = useTranslation();
  return (unit) =>
    unit.standard
      ? [
          t(`units.names.${unit.standard}`),
          t(`units.short.${unit.standard}`),
          unit.name,
          ...unit.aliases,
        ]
      : [unit.name];
}

/**
 * A unit, searched by its names in the reader's words and by a standard
 * unit's aliases ("g", "grams", "kilo"), grouped by what it measures. With
 * `like`, only the units measured like that one are offered. With `none`,
 * choosing no unit is offered under that label and answers null. When what
 * was typed names no unit, a community admin may add it as a unit of the
 * community's own; anyone else picks among those there are.
 */
export function UnitPicker({
  value,
  onChange,
  like,
  none,
  id,
  "aria-label": ariaLabel,
}: {
  value: number | null;
  onChange: (unitId: number | null) => void;
  like?: number | null;
  none?: string;
  id?: string;
  "aria-label"?: string;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const admin = useChangesSettings();
  const foundBy = useFoundBy();
  const units = useListSettingsUnits().data ?? [];
  const [search, setSearch] = useState("");
  const kin = units.find((unit) => unit.id === like);
  const offered = units.filter(
    (unit) => !kin || unit.id === kin.id || (kin.measure !== null && unit.measure === kin.measure)
  );
  const label = (unit: SettingsUnitRead) =>
    unit.standard ? t(`units.names.${unit.standard}`) : unit.name;
  const typed = normalized(search);
  const items: ComboboxItem[] = [
    ...(none && !typed ? [{ value: NONE, label: none }] : []),
    ...offered
      .filter((unit) => !typed || foundBy(unit).some((name) => normalized(name).includes(typed)))
      .map((unit) => ({
        value: String(unit.id),
        label: label(unit),
        hint: unit.standard ? t(`units.short.${unit.standard}`) : undefined,
        group: t(`units.measures.${unit.measure ?? "own"}`),
      })),
  ];
  const named = (name: string) =>
    units.some((unit) => foundBy(unit).some((each) => normalized(each) === normalized(name)));
  const chosen = units.find((unit) => unit.id === value);

  const add = async (name: string) => {
    try {
      const made = await createSettingsUnit({ name });
      await queryClient.invalidateQueries({ queryKey: getListSettingsUnitsQueryKey() });
      onChange(made.id);
    } catch (error) {
      toast.error(errorMessage(error, t, "units.addFailed"));
      throw error;
    }
  };

  return (
    <Combobox
      id={id}
      aria-label={ariaLabel}
      items={items}
      onSearchChange={setSearch}
      debounceMs={0}
      value={value === null ? (none ? NONE : null) : String(value)}
      selectedLabel={chosen ? label(chosen) : value === null ? (none ?? null) : null}
      onValueChange={(next) => onChange(next === NONE ? null : Number(next))}
      placeholder={t("units.choose")}
      searchPlaceholder={t("units.search")}
      emptyMessage={t("units.none")}
      {...(admin
        ? {
            typedLabel: (name: string) =>
              name.trim() && !named(name) ? t("units.useNew", { name: name.trim() }) : "",
            onCreate: add,
          }
        : {})}
    />
  );
}
