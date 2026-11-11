import { Link, useBlocker } from "@tanstack/react-router";
import type { ComponentType } from "react";

import { toast } from "@/components/Toaster";
import { type Blocker, type Host, type HostLinkProps, setHost } from "@/ui/host";
import type { UserValue } from "@/ui/properties/propertyHelpers";

/** The router's blocker: holds a move to another page while `when` says, and
 *  asks before the page reloads or closes then too. */
const useRouterBlocker = (when: () => boolean): Blocker => {
  const blocker = useBlocker({ shouldBlockFn: when, enableBeforeUnload: when, withResolver: true });
  return {
    blocked: blocker.status === "blocked",
    proceed: () => blocker.proceed?.(),
    reset: () => blocker.reset?.(),
  };
};

/** The browser's storage, where the browser lets a framed page keep any. */
const browserStorage = (): Host["storage"] | undefined => {
  try {
    window.localStorage.getItem("");
    return window.localStorage;
  } catch {
    return undefined;
  }
};

/** A person drawn by their name and picture, or by their number when the
 *  value carries neither. */
const drawMember = (person: UserValue) => {
  const name = person.display_name || person.username;
  return {
    id: person.id,
    name: name || `#${person.id}`,
    picture: person.avatar_url ?? undefined,
    initials: name
      ? name
          .split(/\s+/)
          .slice(0, 2)
          .map((word) => word.charAt(0).toUpperCase())
          .join("")
      : "?",
  };
};

/**
 * Hand the UI kit what it takes from Hoard: its notices, links to Hoard's own
 * pages and the guard on leaving one, where a preference is kept and how a
 * person is drawn. Called at boot, before anything renders.
 *
 * Hoard serves its pages and its API from one origin, so the kit's own answer
 * to which origin is Hoard's (the page's) stands. The reader's clock comes
 * over the parent bridge once the session starts.
 */
export const connectKit = (): void => {
  const storage = browserStorage();
  setHost({
    toast,
    Link: Link as unknown as ComponentType<HostLinkProps>,
    useBlocker: useRouterBlocker,
    member: drawMember,
    // Without the browser's, the kit keeps preferences for the visit.
    ...(storage ? { storage } : {}),
  });
};
