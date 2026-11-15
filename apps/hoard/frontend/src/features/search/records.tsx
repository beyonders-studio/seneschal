import { useNavigate } from "@tanstack/react-router";
import type { MouseEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { SearchHit } from "@/api/generated/hoard.schemas";

/** The kinds of record search finds, in the order their tabs are offered. */
export const KINDS = [
  "material",
  "base",
  "product",
  "lot",
  "run",
  "purchase",
  "order",
  "stocktake",
  "hold",
  "location",
] as const;

/** What a kind of record is called, one of them. */
export function useKindName(): (kind: string) => string {
  const { t } = useTranslation("search");
  return (kind) => t(`kinds.${kind}`, { defaultValue: kind });
}

/** Opens the page of a record found, in Hoard's own history. */
export function useOpenRecord(): (hit: Pick<SearchHit, "path">) => void {
  const navigate = useNavigate();
  return (hit) => void navigate({ href: hit.path });
}

/** A link to the page of a record found. A click with a key held is the
 *  browser's own; a plain one opens it in Hoard's history. */
export function RecordLink({ hit, children }: { hit: SearchHit; children: ReactNode }) {
  const open = useOpenRecord();
  const click = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    open(hit);
  };
  return (
    <a href={hit.path} onClick={click} className="font-medium hover:underline">
      {children}
    </a>
  );
}
