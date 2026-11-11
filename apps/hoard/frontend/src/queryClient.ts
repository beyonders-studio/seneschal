import { QueryClient } from "@tanstack/react-query";

/**
 * The cache every page's reads share. A read is fresh for half a minute, so
 * moving between pages does not ask again at once; a failed read is tried once
 * more before its page says so.
 */
export const createQueryClient = () =>
  new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, retry: 1 } } });
