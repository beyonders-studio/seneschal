import { useQueries } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import type { MemberName } from "@/api/generated/hoard.schemas";
import { getListMemberNamesQueryOptions } from "@/api/generated/members/members";

/** The most references one lookup names (the API's bound). */
const PER_LOOKUP = 100;
/** How long a name is used before it is asked for again. */
const NAMES_FRESH_MS = 5 * 60_000;

/**
 * Names for the members `refs` name, as Hoard's records keep them: by
 * Initiative's reference for each, or null for Hoard itself. Answers a
 * function giving each one's name: the name they go by in the community, "former
 * member" for a reference that no longer names anyone, "Hoard" for null, and
 * "a member" when Initiative could not be asked. Blank while names load.
 */
export function useMemberNames(
  refs: ReadonlyArray<string | null | undefined>
): (ref: string | null | undefined) => string {
  const { t } = useTranslation("connection");
  const wanted = [...new Set(refs.filter((ref): ref is string => !!ref))].sort();
  const lookups = useQueries({
    queries: Array.from({ length: Math.ceil(wanted.length / PER_LOOKUP) }, (_, index) =>
      getListMemberNamesQueryOptions(
        { member_ref: wanted.slice(index * PER_LOOKUP, (index + 1) * PER_LOOKUP) },
        { query: { staleTime: NAMES_FRESH_MS } }
      )
    ),
  });
  const named = new Map<string, MemberName>();
  for (const lookup of lookups)
    for (const member of lookup.data ?? []) named.set(member.member_ref, member);
  const failed = lookups.some((lookup) => lookup.isError);

  return (ref) => {
    if (!ref) return t("members.hoard");
    const member = named.get(ref);
    if (member) return member.name ?? t("members.former");
    return failed ? t("members.unknown") : "";
  };
}
