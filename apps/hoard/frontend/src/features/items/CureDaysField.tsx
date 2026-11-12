import { useTranslation } from "react-i18next";

import { Field } from "@/ui/field";
import { Input } from "@/ui/input";

/** Days typed into {@link CureDaysField}, or null for none. */
export const days = (typed: string): number | null =>
  typed.trim() === "" ? null : Number.parseInt(typed, 10);

/** How long what a run makes of an item cures, in whole days; empty for none. */
export function CureDaysField({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation("catalog");
  return (
    <Field label={t("items.cureDays")} hint={t("items.cureDaysHint")}>
      <Input
        type="number"
        min={1}
        step={1}
        inputMode="numeric"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  );
}

/** A cure time, as an item's page shows it. */
export function CureDays({ days }: { days: number | null }) {
  const { t } = useTranslation("catalog");
  return days !== null && t("items.cureDaysValue", { count: days });
}
