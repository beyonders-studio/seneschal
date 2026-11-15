import { keepPreviousData, useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate } from "@tanstack/react-router";
import { Search } from "lucide-react";
import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { SearchHit, Suggestions } from "@/api/generated/hoard.schemas";
import { getSuggestRecordsQueryOptions, useSuggestRecords } from "@/api/generated/search/search";
import { useKindName, useOpenRecord } from "@/features/search/records";
import { Input } from "@/ui/input";
import { useDebouncedValue } from "@/ui/lib/useDebouncedValue";
import { cn } from "@/ui/lib/utils";

/** How long typing pauses before suggestions are asked for. */
const SUGGEST_AFTER_MS = 150;

/** The suggestions as the list shows them: exact codes first, then the rest
 *  grouped by kind, each group in the order its first record was found. */
function grouped(hits: SearchHit[]): { key: string; exact: boolean; hits: SearchHit[] }[] {
  const groups = new Map<string, { key: string; exact: boolean; hits: SearchHit[] }>();
  for (const hit of hits) {
    const key = hit.exact ? "" : hit.kind;
    const group = groups.get(key) ?? { key, exact: hit.exact, hits: [] };
    group.hits.push(hit);
    groups.set(key, group);
  }
  return [...groups.values()].sort((a, b) => Number(b.exact) - Number(a.exact));
}

/**
 * The search field at the top of every Hoard page. Ctrl/Cmd-K focuses it.
 * Typing suggests records, grouped by kind, exact codes first; the arrow keys
 * move through them. Enter opens the one chosen; with none chosen, it opens
 * the record whose code the query is, when exactly one is, so scanning a
 * label goes straight to it; otherwise it opens every result.
 */
export function SearchField() {
  const { t } = useTranslation("search");
  const kindName = useKindName();
  const navigate = useNavigate();
  const queries = useQueryClient();
  const openRecord = useOpenRecord();
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  // On the results page the field holds what it shows results for.
  const searched = useLocation({
    select: (location) =>
      location.pathname === "/search" ? String((location.search as { q?: string }).q ?? "") : "",
  });
  const [text, setText] = useState(searched);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const query = useDebouncedValue(text.trim(), SUGGEST_AFTER_MS);
  const suggestions = useSuggestRecords(
    { q: query },
    { query: { enabled: query.length > 0, placeholderData: keepPreviousData } }
  );

  useEffect(() => setText(searched), [searched]);

  useEffect(() => {
    const focus = (event: globalThis.KeyboardEvent) => {
      if (event.key.toLowerCase() !== "k" || !(event.ctrlKey || event.metaKey)) return;
      event.preventDefault();
      input.current?.focus();
      input.current?.select();
    };
    window.addEventListener("keydown", focus);
    return () => window.removeEventListener("keydown", focus);
  }, []);

  const groups = grouped(suggestions.data?.items ?? []);
  const shown = groups.flatMap((group) => group.hits);
  const listed = open && text.trim().length > 0 && suggestions.data !== undefined;

  const close = () => {
    setOpen(false);
    setActive(-1);
  };

  const go = (hit: SearchHit) => {
    close();
    setText("");
    openRecord(hit);
  };

  const enter = async (typed: string) => {
    let answer: Suggestions | undefined;
    try {
      answer = await queries.fetchQuery(getSuggestRecordsQueryOptions({ q: typed }));
    } catch {
      answer = undefined;
    }
    const [first] = answer?.items ?? [];
    if (answer?.exact_match && first) return go(first);
    close();
    void navigate({ to: "/search", search: { q: typed } });
  };

  const keyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    switch (event.key) {
      case "ArrowDown":
      case "ArrowUp": {
        if (!shown.length) return;
        event.preventDefault();
        setOpen(true);
        const step = event.key === "ArrowDown" ? 1 : -1;
        // From none, through each suggestion, and back to none.
        setActive((current) => ((current + 1 + step + shown.length + 1) % (shown.length + 1)) - 1);
        return;
      }
      case "Enter": {
        event.preventDefault();
        const chosen = listed ? shown[active] : undefined;
        if (chosen) return go(chosen);
        const typed = text.trim();
        if (typed) void enter(typed);
        return;
      }
      case "Escape":
        if (open) {
          event.preventDefault();
          close();
        } else {
          input.current?.blur();
        }
        return;
    }
  };

  const optionId = (index: number) => `${listId}-${index}`;
  let index = -1;

  return (
    <div className="relative w-full max-w-xl">
      <Search
        aria-hidden
        className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
      />
      <Input
        ref={input}
        type="search"
        role="combobox"
        aria-label={t("field.label")}
        aria-expanded={listed}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={listed && active >= 0 ? optionId(active) : undefined}
        placeholder={t("field.placeholder")}
        className="pl-9"
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          setOpen(true);
          setActive(-1);
        }}
        onFocus={() => setOpen(true)}
        onBlur={close}
        onKeyDown={keyDown}
      />
      {listed && (
        <div
          id={listId}
          role="listbox"
          aria-label={t("field.suggestions")}
          className="absolute top-full right-0 left-0 z-50 mt-1 max-h-96 overflow-y-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md"
        >
          {shown.length === 0 && !suggestions.isFetching && (
            <p className="px-2 py-1.5 text-sm text-muted-foreground">{t("field.nothing")}</p>
          )}
          {groups.map((group) => (
            // biome-ignore lint/a11y/useSemanticElements: a listbox groups its options by this role
            <div
              key={group.key}
              role="group"
              aria-label={group.exact ? t("field.exact") : kindName(group.key)}
            >
              <div className="px-2 pt-2 pb-1 text-xs font-medium text-muted-foreground">
                {group.exact ? t("field.exact") : kindName(group.key)}
              </div>
              {group.hits.map((hit) => {
                index += 1;
                const at = index;
                return (
                  <div
                    key={`${hit.kind}-${hit.record_id}`}
                    id={optionId(at)}
                    role="option"
                    // The field keeps the focus; the arrow keys move through the options.
                    tabIndex={-1}
                    aria-selected={at === active}
                    className={cn(
                      "flex cursor-default items-baseline gap-2 rounded-sm px-2 py-1.5 text-sm",
                      at === active && "bg-accent text-accent-foreground"
                    )}
                    // Chosen before the field loses focus and closes the list.
                    onMouseDown={(event) => {
                      event.preventDefault();
                      go(hit);
                    }}
                    onMouseEnter={() => setActive(at)}
                  >
                    <span className="truncate">{hit.title}</span>
                    {group.exact && (
                      <span className="text-xs text-muted-foreground">{kindName(hit.kind)}</span>
                    )}
                    {hit.subtitle && (
                      <span className="truncate text-xs text-muted-foreground">{hit.subtitle}</span>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
