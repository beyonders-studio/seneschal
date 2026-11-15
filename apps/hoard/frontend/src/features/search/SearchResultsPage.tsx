import { getRouteApi } from "@tanstack/react-router";
import { Loader2, Search, SearchX } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { SearchHit } from "@/api/generated/hoard.schemas";
import { useSearchRecords } from "@/api/generated/search/search";
import { KINDS, RecordLink, useKindName } from "@/features/search/records";
import { Badge } from "@/ui/badge";
import { Label } from "@/ui/label";
import { PaginationBar } from "@/ui/pagination-bar";
import { StatusMessage } from "@/ui/status-message";
import { Switch } from "@/ui/switch";
import { Tabs, TabsBar, TabsTrigger } from "@/ui/tabs";

const route = getRouteApi("/search");

/** Every kind's tab is "all" when no kind is asked for. */
const ALL = "all";

/** A record found: its title, which opens it, its kind, what else names it,
 *  and its snippet with the matched words marked. */
function Result({ hit }: { hit: SearchHit }) {
  const { t } = useTranslation("search");
  const kindName = useKindName();
  return (
    <li className="flex flex-col gap-1 py-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <RecordLink hit={hit}>{hit.title}</RecordLink>
        <Badge variant="secondary">{kindName(hit.kind)}</Badge>
        {hit.archived && <Badge variant="outline">{t("results.archived")}</Badge>}
        {hit.subtitle && <span className="text-sm text-muted-foreground">{hit.subtitle}</span>}
      </div>
      {hit.codes.length > 0 && (
        <p className="text-xs text-muted-foreground tabular-nums">{hit.codes.join(" · ")}</p>
      )}
      {hit.snippet.length > 0 && (
        <p className="text-sm text-muted-foreground">
          {hit.snippet.map((part, index) =>
            part.marked ? (
              // biome-ignore lint/suspicious/noArrayIndexKey: a snippet's stretches have no id
              <mark key={index} className="bg-transparent font-medium text-foreground">
                {part.text}
              </mark>
            ) : (
              // biome-ignore lint/suspicious/noArrayIndexKey: a snippet's stretches have no id
              <span key={index}>{part.text}</span>
            )
          )}
        </p>
      )}
    </li>
  );
}

/**
 * The results of a search: a tab for each kind of record found with how many
 * were, the results with their snippets, a switch to include archived
 * records, the close matches when nothing matched, and a note of the syntax.
 */
export function SearchResultsPage() {
  const { t } = useTranslation("search");
  const kindName = useKindName();
  const navigate = route.useNavigate();
  const { q = "", kind, archived, page = 1 } = route.useSearch();
  const query = q.trim();
  const results = useSearchRecords(
    { q: query, ...(kind ? { kind } : {}), ...(archived ? { archived } : {}), page },
    { query: { enabled: query.length > 0 } }
  );

  const counts = results.data?.counts ?? {};
  const kinds = [
    ...KINDS,
    ...Object.keys(counts).filter((found) => !KINDS.includes(found as never)),
  ].filter((found) => counts[found] || found === kind);
  const all = Object.values(counts).reduce((sum, count) => sum + count, 0);

  const header = (
    <div className="flex flex-col gap-3">
      <h2 className="text-xl font-semibold">
        {query ? t("results.heading", { query }) : t("results.title")}
      </h2>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs
          value={kind ?? ALL}
          onValueChange={(value) =>
            void navigate({
              search: (prev) => ({
                ...prev,
                kind: value === ALL ? undefined : value,
                page: undefined,
              }),
            })
          }
        >
          <TabsBar aria-label={t("results.kinds")}>
            <TabsTrigger value={ALL}>
              {t("results.all")}
              <span className="ml-1.5 text-muted-foreground tabular-nums">{all}</span>
            </TabsTrigger>
            {kinds.map((found) => (
              <TabsTrigger key={found} value={found}>
                {t(`tabs.${found}`, { defaultValue: kindName(found) })}
                <span className="ml-1.5 text-muted-foreground tabular-nums">
                  {counts[found] ?? 0}
                </span>
              </TabsTrigger>
            ))}
          </TabsBar>
        </Tabs>
        <div className="flex items-center gap-2">
          <Switch
            id="search-archived"
            checked={archived === true}
            onCheckedChange={(checked) =>
              void navigate({
                search: (prev) => ({
                  ...prev,
                  archived: checked ? true : undefined,
                  page: undefined,
                }),
              })
            }
          />
          <Label htmlFor="search-archived">{t("results.includeArchived")}</Label>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">{t("results.syntax")}</p>
    </div>
  );

  if (!query) {
    return (
      <div className="flex flex-col gap-4">
        {header}
        <StatusMessage icon={<Search />} title={t("results.empty")} />
      </div>
    );
  }

  const data = results.data;
  return (
    <div className="flex flex-col gap-4">
      {header}
      {results.isPending && (
        <StatusMessage icon={<Loader2 className="animate-spin" />} title={t("results.loading")} />
      )}
      {results.isError && <StatusMessage onRetry={() => void results.refetch()} />}
      {data && data.items.length > 0 && (
        <>
          <ul className="divide-y">
            {data.items.map((hit) => (
              <Result key={`${hit.kind}-${hit.record_id}`} hit={hit} />
            ))}
          </ul>
          <PaginationBar
            label={t("results.pages")}
            page={data.page}
            pageSize={data.page_size}
            totalCount={data.total}
            hasNext={data.page * data.page_size < data.total}
            onPageChange={(next) =>
              void navigate({ search: (prev) => ({ ...prev, page: next > 1 ? next : undefined }) })
            }
          />
        </>
      )}
      {data && data.items.length === 0 && (
        <StatusMessage
          icon={<SearchX />}
          title={t("results.nothing", { query })}
          description={t("results.nothingHint")}
        />
      )}
      {data && data.close_matches.length > 0 && (
        <section className="flex flex-col gap-2" aria-label={t("results.didYouMean")}>
          <h3 className="font-medium">{t("results.didYouMean")}</h3>
          <ul className="flex flex-col gap-1">
            {data.close_matches.map((hit) => (
              <li key={`${hit.kind}-${hit.record_id}`} className="flex items-baseline gap-2">
                <RecordLink hit={hit}>{hit.title}</RecordLink>
                <span className="text-xs text-muted-foreground">{kindName(hit.kind)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
