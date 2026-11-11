import { Plus, Search } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import { useSession } from "@/session";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";
import { Input } from "@/ui/input";
import { useDebouncedValue } from "@/ui/lib/useDebouncedValue";
import { PaginationBar } from "@/ui/pagination-bar";
import { StatusMessage } from "@/ui/status-message";

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

/**
 * A list of items: its search, its button to add one while Hoard may change,
 * the table, and a pager. Until there is anything to list, `empty` shows in
 * its place.
 */
export function ItemList({
  search,
  total,
  addLabel,
  onAdd,
  empty,
  failed,
  onRetry,
  children,
}: {
  search: ReturnType<typeof useItemSearch>;
  /** How many the list holds; undefined while it loads. */
  total: number | undefined;
  addLabel: string;
  onAdd: () => void;
  empty: { icon: ReactNode; title: string; description: string };
  failed: boolean;
  onRetry: () => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const { read_only } = useSession();
  const add = !read_only && (
    <Button onClick={onAdd}>
      <Plus aria-hidden />
      {addLabel}
    </Button>
  );
  if (failed) return <StatusMessage onRetry={onRetry} />;
  if (total === 0 && !search.params.q)
    return <StatusMessage {...empty} action={add || undefined} />;
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
        {add}
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
