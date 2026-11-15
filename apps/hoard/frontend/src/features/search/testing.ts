import type { SearchHit, SearchResults } from "@/api/generated/hoard.schemas";

/** A record found, as search reads it: a lot unless told otherwise. */
export const buildHit = (overrides: Partial<SearchHit> = {}): SearchHit => ({
  kind: "lot",
  record_id: 4,
  title: "LOT-1",
  subtitle: "Oolong",
  path: "/stock/lots/4",
  codes: ["LOT-1"],
  archived: false,
  exact: false,
  snippet: [],
  ...overrides,
});

/** A page of results holding `items`, of every kind found. */
export const buildResults = (overrides: Partial<SearchResults> = {}): SearchResults => ({
  items: [],
  total: 0,
  page: 1,
  page_size: 20,
  counts: {},
  close_matches: [],
  ...overrides,
});
