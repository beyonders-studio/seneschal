import { useSession } from "@/session";

/** Hoard's permission to see costs and prices (the backend's `SEE_COSTS`). */
export const SEE_COSTS = "costs";

/** Whether the reader sees costs and prices where the page was opened. Without
 *  it the API sends none, so a page shows no cost field and takes none. */
export const useSeesCosts = (): boolean => useSession().permissions.includes(SEE_COSTS);

/** Whether the reader administers the community, which changing Hoard's
 *  settings takes. */
export const useIsAdmin = (): boolean => useSession().admin;
