/**
 * The access token expires every 30 minutes; the refresh token is good for 7
 * days. Before this interceptor existed the client stored the refresh token
 * and never used it, so any 401 cleared both and hard-redirected to /login --
 * a guaranteed logout every half hour with a valid refresh token sitting
 * unused in localStorage.
 *
 * These tests pin that behaviour. They MUST fail if the refresh path is
 * removed and a 401 goes straight back to logging the user out.
 *
 * No jsdom and no axios mocking library: vitest runs in the `node`
 * environment here, so localStorage/window are stubbed directly and the
 * transport is replaced with a custom axios adapter. Both are a few lines.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import axios, { type AxiosAdapter } from "axios";

import api, { REFRESH_TOKEN_KEY, TOKEN_KEY } from "@/lib/api";

type Handler = (config: {
  url?: string;
  headers?: Record<string, unknown>;
}) => [number, unknown];

const realApiAdapter = api.defaults.adapter;
const realAxiosAdapter = axios.defaults.adapter;

/** Replace the transport for both `api` and bare axios (the refresh call). */
function transport(handler: Handler) {
  const adapter: AxiosAdapter = async (config) => {
    const [status, data] = handler(config as never);
    const response = {
      data,
      status,
      statusText: "",
      headers: {},
      config,
    } as never;
    if (status >= 200 && status < 300) return response;
    const err = new Error(`Request failed with status code ${status}`);
    Object.assign(err, { isAxiosError: true, config, response });
    throw err;
  };
  api.defaults.adapter = adapter;
  axios.defaults.adapter = adapter;
}

function makeStorage() {
  const store = new Map<string, string>();
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  };
}

const isRefresh = (url?: string) => (url ?? "").includes("/auth/refresh");

beforeEach(() => {
  (globalThis as never as { localStorage: unknown }).localStorage =
    makeStorage();
  (globalThis as never as { window: unknown }).window = {
    location: { href: "/" },
  };
});

afterEach(() => {
  api.defaults.adapter = realApiAdapter;
  axios.defaults.adapter = realAxiosAdapter;
});

const href = () =>
  (globalThis as never as { window: { location: { href: string } } }).window
    .location.href;

describe("401 handling", () => {
  it("refreshes and retries instead of logging out", async () => {
    localStorage.setItem(TOKEN_KEY, "expired");
    localStorage.setItem(REFRESH_TOKEN_KEY, "good-refresh");

    let attempts = 0;
    transport((config) => {
      if (isRefresh(config.url)) {
        return [200, { access_token: "fresh", refresh_token: "fresh-r" }];
      }
      attempts += 1;
      return attempts === 1 ? [401, { detail: "expired" }] : [200, [{ id: 1 }]];
    });

    const res = await api.get("/apps");

    expect(res.status).toBe(200);
    expect(attempts).toBe(2); // original + retry
    expect(localStorage.getItem(TOKEN_KEY)).toBe("fresh");
    expect(localStorage.getItem(REFRESH_TOKEN_KEY)).toBe("fresh-r");
    expect(href()).toBe("/"); // never bounced
  });

  it("logs out when the refresh token is also dead", async () => {
    localStorage.setItem(TOKEN_KEY, "expired");
    localStorage.setItem(REFRESH_TOKEN_KEY, "dead-refresh");

    transport((config) =>
      isRefresh(config.url) ? [401, { detail: "dead" }] : [401, { detail: "x" }],
    );

    await expect(api.get("/apps")).rejects.toBeTruthy();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(localStorage.getItem(REFRESH_TOKEN_KEY)).toBeNull();
    expect(href()).toBe("/login");
  });

  it("does not refresh on a failed login -- that is a bad password", async () => {
    let refreshCalls = 0;
    transport((config) => {
      if (isRefresh(config.url)) {
        refreshCalls += 1;
        return [200, { access_token: "x", refresh_token: "y" }];
      }
      return [401, { detail: "Invalid email or password" }];
    });

    await expect(api.post("/auth/login", {})).rejects.toBeTruthy();
    expect(refreshCalls).toBe(0);
    // A bad password must not reload the page, or the error toast is wiped
    // before it can be read.
    expect(href()).toBe("/");
  });

  it("collapses concurrent 401s into a single refresh", async () => {
    // The server ROTATES the refresh token, so parallel refreshes would race
    // and the loser's token would already be spent.
    localStorage.setItem(TOKEN_KEY, "expired");
    localStorage.setItem(REFRESH_TOKEN_KEY, "good-refresh");

    let refreshCalls = 0;
    transport((config) => {
      if (isRefresh(config.url)) {
        refreshCalls += 1;
        return [200, { access_token: "fresh", refresh_token: "fresh-r" }];
      }
      const auth = String(config.headers?.Authorization ?? "");
      return auth.includes("expired") ? [401, { detail: "expired" }] : [200, []];
    });

    const results = await Promise.all([
      api.get("/apps"),
      api.get("/apps"),
      api.get("/apps"),
      api.get("/apps"),
    ]);

    expect(results.every((r) => r.status === 200)).toBe(true);
    expect(refreshCalls).toBe(1);
  });

  it("gives up after one retry rather than looping forever", async () => {
    localStorage.setItem(TOKEN_KEY, "expired");
    localStorage.setItem(REFRESH_TOKEN_KEY, "good-refresh");

    let attempts = 0;
    transport((config) => {
      if (isRefresh(config.url)) {
        return [200, { access_token: "fresh", refresh_token: "fresh-r" }];
      }
      attempts += 1;
      return [401, { detail: "still expired" }];
    });

    await expect(api.get("/apps")).rejects.toBeTruthy();
    expect(attempts).toBe(2); // original + exactly one retry
    expect(href()).toBe("/login");
  });
});
