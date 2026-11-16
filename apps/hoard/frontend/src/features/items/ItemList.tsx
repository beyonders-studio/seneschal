import { Plus, Search } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import { ItemStatus } from "@/api/generated/hoard.schemas";
import { useSession } from "@/session";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";
import { Label } from "@/ui/label";
import { useDebouncedValue } from "@/ui/lib/useDebouncedValue";
import { PaginationBar } from "@/ui/pagination-bar";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";
import { StatusMessage } from "@/ui/status-message";
import { Switch } from "@/ui/switch";

/** How many items a list shows at once. */
const PAGE_SIZE = 50;

/** What a list of items is searched for, and which page of it is shown. */
export function useItemSearch() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const q = useDebouncedValue(search.trim());
  return {
    search,
    /** Searching starts again from the first page. */
    setSearch: (value: string) => {
      setSearch(value);
      setPage(1);
    },
    page,
    setPage,
    params: { q: q || undefined, offset: (page - 1) * PAGE_SIZE, limit: PAGE_SIZE },
  };
}

/** Whether a list shows its archived items too, and turning that on and off. */
export interface ShowArchived {
  shown: boolean;
  onShownChange: (shown: boolean) => void;
}

/**
 * A list of items: its search, its button to add one while Hoard may change
 * (when it has `onAdd`), a switch to show archived items too (when it has
 * `archived`), the table, and a pager. Until there is anything to list,
 * `empty` shows in its place.
 */
export function ItemList({
  search,
  total,
  addLabel,
  onAdd,
  empty,
  failed,
  onRetry,
  more,
  archived,
  children,
}: {
  search: ReturnType<typeof useItemSearch>;
  /** How many the list holds; undefined while it loads. */
  total: number | undefined;
  addLabel?: string;
  onAdd?: () => void;
  empty: { icon: ReactNode; title: string; description: string };
  failed: boolean;
  onRetry: () => void;
  /** What else can be done with the list, before adding to it. */
  more?: ReactNode;
  archived?: ShowArchived;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const { read_only } = useSession();
  const add = !read_only && onAdd && (
    <Button onClick={onAdd}>
      <Plus aria-hidden />
      {addLabel}
    </Button>
  );
  const showArchived = archived && (
    <div className="flex items-center gap-2">
      <Switch
        id="show-archived"
        checked={archived.shown}
        onCheckedChange={(shown) => {
          archived.onShownChange(shown);
          search.setPage(1);
        }}
      />
      <Label htmlFor="show-archived">{t("catalog:items.showArchived")}</Label>
    </div>
  );
  if (failed) return <StatusMessage onRetry={onRetry} />;
  if (total === 0 && !search.params.q && !archived?.shown)
    return (
      <StatusMessage
        {...empty}
        action={
          (add || showArchived) && (
            <div className="flex flex-wrap items-center justify-center gap-4">
              {add}
              {showArchived}
            </div>
          )
        }
      />
    );
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="relative w-full max-w-xs">
          <Search
            aria-hidden
            className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            type="search"
            aria-label={t("common.search")}
            placeholder={t("common.search")}
            className="pl-8"
            value={search.search}
            onChange={(event) => search.setSearch(event.target.value)}
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {showArchived}
          {more}
          {add}
        </div>
      </div>
      {total === undefined ? (
        <p className="text-sm text-muted-foreground">{t("common.loading")}</p>
      ) : (
        children
      )}
      <PaginationBar
        page={search.page}
        pageSize={PAGE_SIZE}
        totalCount={total ?? 0}
        hasNext={search.page * PAGE_SIZE < (total ?? 0)}
        onPageChange={search.setPage}
      />
    </div>
  );
}

/** Marks stock at or below its low-stock limit. */
export function LowStockBadge() {
  const { t } = useTranslation("catalog");
  return <Badge variant="warning">{t("items.low")}</Badge>;
}

/** Marks an archived product, material or base; an active one carries no mark. */
export function StatusBadge({ status }: { status: ItemStatus }) {
  const { t } = useTranslation("catalog");
  if (status === "active") return null;
  return <Badge variant="outline">{t(`items.statuses.${status}`)}</Badge>;
}

/** Whether a product, material or base is active or archived. */
export function StatusField({
  value,
  onChange,
}: {
  value: ItemStatus;
  onChange: (status: ItemStatus) => void;
}) {
  const { t } = useTranslation("catalog");
  return (
    <Field label={t("items.status")}>
      <Select value={value} onValueChange={(next) => onChange(next as ItemStatus)}>
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Object.values(ItemStatus).map((status) => (
            <SelectItem key={status} value={status}>
              {t(`items.statuses.${status}`)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  );
}
