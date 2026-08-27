import axios from "axios";

const TOKEN_KEY = "aso_access_token";

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

api.interceptors.response.use(
  (response) => response,
  (error) => {
    // ponytail: a 401 from the login call is a bad password, not an expired
    // session — bouncing there reloads the page and eats the error toast.
    if (
      error.response?.status === 401 &&
      !error.config?.url?.endsWith("/auth/login")
    ) {
      localStorage.removeItem(TOKEN_KEY);
      window.location.href = "/login";
    }
    return Promise.reject(error);
  },
);

export { TOKEN_KEY };
export default api;
