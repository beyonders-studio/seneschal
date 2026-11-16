import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { CategoryKind } from "@/api/generated/hoard.schemas";
import {
  createSettingsType,
  getListSettingsTypesQueryKey,
} from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { useChangesSettings } from "@/features/settings/NamedList";
import { useSettingsNames } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
import { Combobox } from "@/ui/combobox";

/**
 * A type of `kind`, searched by name. When what was typed names no type, a
 * community admin may make it a new type there and then, in its kind's first
 * shape; anyone else picks among those there are.
 */
export function TypePicker({
  kind,
  value,
  onChange,
}: {
  kind: CategoryKind;
  value: number | null;
  onChange: (typeId: number) => void;
}) {
  const { t } = useTranslation("catalog");
  const queryClient = useQueryClient();
  const admin = useChangesSettings();
  const types = useSettingsNames().typesOf(kind);
  const [search, setSearch] = useState("");
  const typed = search.trim().toLowerCase();
  const named = (name: string) =>
    types.some((type) => type.name.toLowerCase() === name.trim().toLowerCase());

  const add = async (name: string) => {
    try {
      const made = await createSettingsType({ kind, name: name.trim() });
      await queryClient.invalidateQueries({ queryKey: getListSettingsTypesQueryKey() });
      onChange(made.id);
    } catch (error) {
      toast.error(errorMessage(error, t, "types.addFailed"));
      throw error;
    }
  };

  return (
    <Combobox
      items={types
        .filter((type) => !typed || type.name.toLowerCase().includes(typed))
        .map((type) => ({ value: String(type.id), label: type.name }))}
      onSearchChange={setSearch}
      debounceMs={0}
      value={value === null ? null : String(value)}
      selectedLabel={types.find((type) => type.id === value)?.name ?? null}
      onValueChange={(next) => onChange(Number(next))}
      placeholder={t("types.choose")}
      searchPlaceholder={t("types.search")}
      emptyMessage={t("types.none")}
      {...(admin
        ? {
            typedLabel: (name: string) =>
              name.trim() && !named(name) ? t("types.useNew", { name: name.trim() }) : "",
            onCreate: add,
          }
        : {})}
    />
  );
}
