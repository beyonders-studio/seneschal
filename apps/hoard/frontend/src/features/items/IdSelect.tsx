import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";

/** The value that stands for no choice, which a select item cannot carry as empty. */
const NONE = "none";

/**
 * A choice of one record by its id, among `options`. With `none`, choosing
 * nothing is offered under that label and answers null.
 */
export function IdSelect({
  value,
  options,
  onChange,
  none,
}: {
  value: number | null;
  options: { id: number; name: string }[];
  onChange: (id: number | null) => void;
  none?: string;
}) {
  return (
    <Select
      value={value === null ? (none ? NONE : undefined) : String(value)}
      onValueChange={(chosen) => onChange(chosen === NONE ? null : Number(chosen))}
    >
      <SelectTrigger>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {none && <SelectItem value={NONE}>{none}</SelectItem>}
        {options.map((option) => (
          <SelectItem key={option.id} value={String(option.id)}>
            {option.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
