import { type QueryKey, useMutation, useQueryClient } from "@tanstack/react-query";

import { toast } from "@/components/Toaster";
import { useChangesSettings } from "@/features/settings/NamedList";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";

/**
 * One of a setting's choices, such as a category's picking rule, which an
 * admin picks and Hoard saves at once; everyone else reads it. Saving
 * refreshes `queryKey`, and a refusal says `failed`.
 */
export function SettingChoice<V extends string>({
  label,
  value,
  choices,
  save,
  queryKey,
  failed,
}: {
  label: string;
  value: V;
  choices: { value: V; label: string }[];
  save: (value: V) => Promise<unknown>;
  queryKey: QueryKey;
  failed: (error: unknown) => string;
}) {
  const queries = useQueryClient();
  const changes = useChangesSettings();
  const saving = useMutation({
    mutationFn: save,
    onSuccess: () => queries.invalidateQueries({ queryKey }),
    onError: (error) => toast.error(failed(error)),
  });
  return (
    <Select
      value={value}
      onValueChange={(next) => {
        const chosen = choices.find((each) => each.value === next);
        if (chosen) saving.mutate(chosen.value);
      }}
      disabled={!changes || saving.isPending}
    >
      <SelectTrigger aria-label={label} className="min-w-48">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {choices.map((choice) => (
          <SelectItem key={choice.value} value={choice.value}>
            {choice.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
