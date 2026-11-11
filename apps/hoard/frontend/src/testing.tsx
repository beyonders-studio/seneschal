import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from "axios";

import type { Me } from "@/api/generated/hoard.schemas";
import { apiClient } from "@/api/mutator";
import { createAppRouter } from "@/router";

/** Who `/auth/me` says is signed in: a member opening Hoard in an initiative. */
export const signedIn = (overrides: Partial<Me> = {}): Me => ({
  member_ref: "member-ref",
  community_ref: "community-ref",
  initiative_id: 7,
  page: "stock",
  admin: false,
  permissions: ["use"],
  theme: "light",
  locale: "en",
  read_only: false,
  plan: "standard",
  ...overrides,
});

export interface Answer {
  status: number;
  data?: unknown;
}

/** Answers Hoard's API by `"<METHOD> <path>"` (without the query); returns the
 *  requests it was asked. */
export function answerApi(answers: Record<string, Answer>): InternalAxiosRequestConfig[] {
  const asked: InternalAxiosRequestConfig[] = [];
  apiClient.defaults.adapter = async (config) => {
    asked.push(config);
    const { status, data } = answers[`${config.method?.toUpperCase()} ${config.url}`] ?? {
      status: 404,
    };
    const response: AxiosResponse = { data, status, statusText: "", headers: {}, config };
    if (status >= 400) throw new AxiosError("refused", undefined, config, null, response);
    return response;
  };
  return asked;
}

/** Opens Hoard at `path`, framed in Initiative, and waits until it asks for the handoff.
 *  Each opening has a cache of its own, and a failed read is not tried again. */
export async function openPage(path: string) {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queries}>
      <RouterProvider router={createAppRouter(createMemoryHistory({ initialEntries: [path] }))} />
    </QueryClientProvider>
  );
  await screen.findByText("Connecting to Initiative…");
}

/** Sends Initiative's handoff, as its plug-in page does. */
export const sendHandoff = (fields: Record<string, unknown> = {}) =>
  post({ type: "initiative-plugin:handoff", handoff_token: "handoff", locale: "en", ...fields });

/** A message from Initiative. */
export const post = (data: Record<string, unknown>) =>
  window.dispatchEvent(new MessageEvent("message", { data }));

/** Opens Hoard at `path` with the session `/auth/me` gives, answering the
 *  page's other calls from `answers`; returns the requests it was asked. */
export async function openSignedIn(
  path: string,
  me: Me = signedIn(),
  answers: Record<string, Answer> = {}
) {
  const asked = answerApi({
    "POST /api/v1/auth/handoff/exchange": { status: 200, data: me },
    "GET /api/v1/auth/me": { status: 200, data: me },
    ...answers,
  });
  await openPage(path);
  sendHandoff();
  return asked;
}
