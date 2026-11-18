import { fireEvent, screen, waitFor } from "@testing-library/react";
import axios from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { answerApi, openPageAt, signedIn } from "@/testing";

// Hoard as its phone app, with the phone's plug-ins standing in.
vi.mock("@seneschal/app/native/platform", () => ({ isNative: () => true }));

const phone = vi.hoisted(() => {
  const groups = new Map<string, Map<string, string>>();
  let group = "CapacitorStorage";
  const kept = () => {
    if (!groups.has(group)) groups.set(group, new Map());
    return groups.get(group) as Map<string, string>;
  };
  const links: ((event: { url: string }) => void)[] = [];
  return {
    groups,
    links,
    Preferences: {
      configure: async (options: { group: string }) => {
        group = options.group;
      },
      keys: async () => ({ keys: [...kept().keys()] }),
      get: async ({ key }: { key: string }) => ({ value: kept().get(key) ?? null }),
      set: async ({ key, value }: { key: string; value: string }) => {
        kept().set(key, value);
      },
      remove: async ({ key }: { key: string }) => {
        kept().delete(key);
      },
    },
    opened: [] as string[],
  };
});

vi.mock("@capacitor/preferences", () => ({ Preferences: phone.Preferences }));
vi.mock("@capacitor/browser", () => ({
  Browser: {
    open: async ({ url }: { url: string }) => {
      phone.opened.push(url);
    },
    close: async () => {},
  },
}));
vi.mock("@capacitor/app", () => ({
  App: {
    addListener: async (event: string, listener: (event: { url: string }) => void) => {
      if (event === "appUrlOpen") phone.links.push(listener);
      return { remove: async () => {} };
    },
    getLaunchUrl: async () => undefined,
    exitApp: async () => {},
  },
}));

const CLOUD = "https://hoard.initiativetasks.com";
const DEVICE_REFRESH = "POST /api/v1/auth/device/refresh";
const DEVICE_TOKEN = "POST /api/v1/auth/device/token";

/** What the phone keeps, before the app starts. */
async function keep(credentials: Record<string, string>, settings: Record<string, string>) {
  phone.groups.clear();
  phone.groups.set("SeneschalCredentials", new Map(Object.entries(credentials)));
  phone.groups.set("CapacitorStorage", new Map(Object.entries(settings)));
  const { loadStorage } = await import("@seneschal/app/native/storage");
  await loadStorage();
}

const deviceSession = (overrides = {}) => ({
  session: "device-session",
  session_expires_at: new Date(Date.now() + 600_000).toISOString(),
  renewal: "renewal-2",
  me: signedIn(),
  ...overrides,
});

describe("Hoard as its phone app", () => {
  beforeEach(() => {
    phone.links.length = 0;
    phone.opened.length = 0;
  });

  it("asks which server first, and takes Initiative's cloud once it answers as Hoard", async () => {
    await keep({}, {});
    answerApi({});
    const asked: string[] = [];
    axios.defaults.adapter = async (config) => {
      asked.push(config.url ?? "");
      return {
        data: { app: "hoard", browser: true, device: true },
        status: 200,
        statusText: "",
        headers: {},
        config,
      };
    };
    openPageAt("/stock");

    fireEvent.click(await screen.findByRole("button", { name: "Use Initiative's cloud" }));

    await screen.findByText("You're not signed in to Hoard");
    expect(asked).toEqual([`${CLOUD}/api/v1/auth/sign-in`]);
    expect(phone.groups.get("CapacitorStorage")?.get("seneschal-server")).toBe(CLOUD);
  });

  it("refuses a server that doesn't answer as Hoard", async () => {
    await keep({}, {});
    answerApi({});
    axios.defaults.adapter = async (config) => ({
      data: { app: "elsewhere", browser: true, device: true },
      status: 200,
      statusText: "",
      headers: {},
      config,
    });
    openPageAt("/stock");

    fireEvent.change(await screen.findByLabelText("Another address"), {
      target: { value: "elsewhere.example" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await screen.findByText("That address doesn't answer as Hoard.");
  });

  it("opens on the sign-in it holds, renewed from this install, with Bearer calls", async () => {
    await keep(
      { "seneschal-renewal": "renewal-1" },
      { "seneschal-server": CLOUD, "seneschal-install": "install-0001" }
    );
    const asked = answerApi({
      [DEVICE_REFRESH]: { status: 200, data: deviceSession() },
      "GET /api/v1/auth/me": { status: 200, data: signedIn() },
    });
    openPageAt("/stock");

    await screen.findByRole("heading", { name: "Stock" });
    expect(screen.getByRole("navigation", { name: "Hoard's pages" })).toBeTruthy();
    const renewal = asked.find((request) => request.url === "/api/v1/auth/device/refresh");
    expect(JSON.parse(renewal?.data)).toEqual({ renewal: "renewal-1", install: "install-0001" });
    expect(renewal?.baseURL).toBe(CLOUD);
    const later = asked.find((request) => !request.url?.startsWith("/api/v1/auth/device/"));
    expect(later?.headers.get("Authorization")).toBe("Bearer device-session");
    expect(phone.groups.get("SeneschalCredentials")?.get("seneschal-renewal")).toBe("renewal-2");
  });

  it("signs in through the system browser and takes the code it brings back", async () => {
    await keep({}, { "seneschal-server": CLOUD, "seneschal-install": "install-0001" });
    const asked = answerApi({ [DEVICE_TOKEN]: { status: 200, data: deviceSession() } });
    openPageAt("/stock");

    fireEvent.click(await screen.findByRole("button", { name: "Sign in with Initiative" }));
    await waitFor(() => expect(phone.opened).toHaveLength(1));
    const started = new URL(phone.opened[0]);
    expect(`${started.origin}${started.pathname}`).toBe(`${CLOUD}/api/v1/auth/sign-in/start`);
    expect(started.searchParams.get("client")).toBe("device");
    expect(started.searchParams.get("install")).toBe("install-0001");
    const state = started.searchParams.get("state");

    // A callback for a sign-in this app didn't begin is ignored.
    for (const open of phone.links) open({ url: "hoard://sign-in/callback?code=x&state=forged" });
    for (const open of phone.links)
      open({ url: `hoard://sign-in/callback?code=c-1&state=${state}` });

    await screen.findByRole("heading", { name: "Stock" });
    const spent = asked.filter((request) => request.url === "/api/v1/auth/device/token");
    expect(spent).toHaveLength(1);
    const body = JSON.parse(spent[0].data);
    expect(body.code).toBe("c-1");
    expect(body.install).toBe("install-0001");
    expect(body.verifier).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });

  it("says why a sign-in came back without a session", async () => {
    await keep({}, { "seneschal-server": CLOUD, "seneschal-install": "install-0001" });
    answerApi({});
    openPageAt("/stock");
    fireEvent.click(await screen.findByRole("button", { name: "Sign in with Initiative" }));
    await waitFor(() => expect(phone.opened).toHaveLength(1));
    const state = new URL(phone.opened[0]).searchParams.get("state");

    for (const open of phone.links) {
      open({ url: `hoard://sign-in/callback?error=SIGN_IN_CANCELLED&state=${state}` });
    }

    await screen.findByText("You didn't finish signing in with Initiative.");
  });
});
