import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { HoardPage } from "@/components/HoardPage";
import { SearchResultsPage } from "@/features/search/SearchResultsPage";

/** What the results page shows: the query, the kind of record, whether
 *  archived records are included, and the page. */
export interface SearchParams {
  q?: string;
  kind?: string;
  archived?: true;
  page?: number;
}

/** Every result of a search, from the field at the top of every page. */
export const Route = createFileRoute("/search")({
  component: Search,
  validateSearch: (search: Record<string, unknown>): SearchParams => {
    const page = Number(search.page);
    return {
      ...(typeof search.q === "string" && search.q.trim() ? { q: search.q } : {}),
      ...(typeof search.kind === "string" && search.kind ? { kind: search.kind } : {}),
      ...(search.archived === true || search.archived === "true" ? { archived: true } : {}),
      ...(Number.isInteger(page) && page > 1 ? { page } : {}),
    };
  },
});

function Search() {
  const { t } = useTranslation("search");
  return (
    <HoardPage title={t("results.title")}>
      <SearchResultsPage />
    </HoardPage>
  );
}
