import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";

const TOKEN_KEY = "aso_access_token";
const REFRESH_TOKEN_KEY = "aso_refresh_token";

const api = axios.create({
  baseURL: "/api/v1",
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

/**
 * In-flight refresh, shared by every request that 401s at once.
 *
 * The server ROTATES the refresh token on each use, so two concurrent
 * refreshes would race and the loser's token would already be spent. One
 * promise, awaited by all callers, keeps that to a single exchange.
 */
let refreshInFlight: Promise<string> | null = null;

function refreshAccessToken(): Promise<string> {
  if (refreshInFlight) return refreshInFlight;

  const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
  if (!refreshToken) return Promise.reject(new Error("no refresh token"));

  // Bare axios, not `api` — going through the instance would attach the dead
  // access token and recurse back into this interceptor.
  refreshInFlight = axios
    .post("/api/v1/auth/refresh", { refresh_token: refreshToken })
    .then(({ data }) => {
      localStorage.setItem(TOKEN_KEY, data.access_token);
      localStorage.setItem(REFRESH_TOKEN_KEY, data.refresh_token);
      return data.access_token as string;
    })
    .finally(() => {
      refreshInFlight = null;
    });

  return refreshInFlight;
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  window.location.href = "/login";
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as
      | (InternalAxiosRequestConfig & { _retried?: boolean })
      | undefined;
    const url = config?.url ?? "";

    // A 401 from login is a bad password, not an expired session — bouncing
    // there reloads the page and eats the error toast. A 401 from refresh
    // means the refresh token itself is dead; there is nothing left to try.
    const isAuthCall =
      url.endsWith("/auth/login") || url.endsWith("/auth/refresh");

    if (error.response?.status !== 401 || isAuthCall || !config) {
      return Promise.reject(error);
    }

    // The access token expires every 30 minutes while the refresh token is
    // good for 7 days. Spend the refresh token before logging anyone out —
    // otherwise every session ends after half an hour for no reason.
    if (config._retried) {
      logout();
      return Promise.reject(error);
    }
    config._retried = true;

    try {
      const token = await refreshAccessToken();
      config.headers.Authorization = `Bearer ${token}`;
      return api(config);
    } catch (refreshError) {
      logout();
      return Promise.reject(refreshError);
    }
  },
);

export { TOKEN_KEY, REFRESH_TOKEN_KEY };
export default api;
