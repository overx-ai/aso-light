/**
 * TanStack Query hooks for Custom Product Pages (CPP) + the visual
 * old-vs-new screenshot comparison.
 *
 * Kept in a dedicated module (rather than the monolithic ``hooks.ts``)
 * because these are the only consumers of the CPP REST surface added for
 * the Compare page. They follow the same conventions as ``hooks.ts``:
 * every call goes through the shared ``api`` axios client (JWT attached by
 * its request interceptor), query keys are namespaced, and mutations emit
 * Mantine notifications.
 *
 * The compare endpoint returns a binary ``image/png``. Because the API is
 * authed via a ``Bearer`` header (not a cookie), the PNG cannot be loaded
 * with a bare ``<img src>`` pointing at the REST URL — the request would go
 * out unauthenticated. Instead :func:`useCompareImage` fetches the PNG as a
 * blob through ``api`` (so the interceptor attaches the token) and exposes a
 * short-lived object URL the page binds to an ``<img>``.
 *
 * :func:`useDefaultScreenshots` backs the in-browser, upload-based compare:
 * it returns the live DEFAULT product page screenshots as JSON (slot index +
 * CDN ``source_url``), used as the "before (current)" side. Those CDN URLs
 * are public Apple-rendered images, so they load with a bare ``<img src>``
 * (no auth header), unlike the compare PNG above.
 *
 * :func:`useCreateCppFromUpload` turns the uploaded "after" File[] into a
 * brand-new Custom Product Page: it posts the files as multipart/form-data
 * to the ``/cpps/from-upload`` endpoint, which creates the page, ensures a
 * localization, and uploads each screenshot to App Store Connect.
 */
import { useEffect } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { notifications } from "@mantine/notifications";
import api from "@/lib/api";

// ---- Types ----

export interface CPP {
  id: string;
  name: string | null;
  visible: boolean | null;
}

export interface CPPListResponse {
  items: CPP[];
}

export interface CPPCreateIn {
  name: string;
  visible: boolean;
}

export interface CPPFromUploadIn {
  name: string;
  locale: string;
  displayType: string;
  files: File[];
}

export interface CPPFromUploadResponse {
  cpp_id: string;
  name: string | null;
  uploaded_count: number;
}

export interface DefaultScreenshot {
  slot: number;
  source_url: string;
  file_name: string | null;
}

export interface DefaultScreenshotsResponse {
  items: DefaultScreenshot[];
}

// ---- Error helpers ----

/**
 * Pull the server-supplied ``detail`` off an axios error body, falling back
 * to ``fallback`` when the shape doesn't match (network error, non-JSON, …).
 * Used by the mutation ``onError`` notifications below.
 */
function errorDetail(error: unknown, fallback: string): string {
  return (
    (error as { response?: { data?: { detail?: string } } })?.response?.data
      ?.detail ?? fallback
  );
}

// ---- Query Keys ----

export const cppQueryKeys = {
  cpps: (appId: number) => ["cpps", appId] as const,
  compare: (
    appId: number,
    cppId: string,
    locale: string,
    displayType: string,
  ) => ["cpp-compare", appId, cppId, locale, displayType] as const,
  defaultScreenshots: (
    appId: number,
    locale: string,
    displayType: string,
  ) => ["default-screenshots", appId, locale, displayType] as const,
};

// ---- CPP list / create ----

export function useCpps(appId: number) {
  return useQuery({
    queryKey: cppQueryKeys.cpps(appId),
    queryFn: async (): Promise<CPP[]> => {
      const response = await api.get<CPPListResponse>(`/apps/${appId}/cpps`);
      return response.data.items;
    },
    enabled: appId > 0,
  });
}

export function useCreateCpp(appId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CPPCreateIn): Promise<CPP> => {
      const response = await api.post<CPP>(`/apps/${appId}/cpps`, body);
      return response.data;
    },
    onSuccess: (cpp) => {
      queryClient.invalidateQueries({ queryKey: cppQueryKeys.cpps(appId) });
      notifications.show({
        title: "Custom Product Page created",
        message: `"${cpp.name ?? cpp.id}" added. Add localized screenshots in App Store Connect.`,
        color: "green",
      });
    },
    onError: (error: unknown) => {
      notifications.show({
        title: "Create failed",
        message: errorDetail(error, "Could not create the Custom Product Page."),
        color: "red",
      });
    },
  });
}

/**
 * Create a Custom Product Page directly from an uploaded "after" set.
 *
 * Posts the proposed File[] (plus name, locale, and display type) as
 * multipart/form-data to ``POST /apps/{appId}/cpps/from-upload``. The backend
 * creates the page, ensures a localization for the locale exists, and uploads
 * each screenshot to App Store Connect. On success the CPP list query is
 * invalidated so the new page shows up in the Custom Product Page tab, and a
 * notification reports the new page (it can then be attached to Apple Search
 * Ads ad groups).
 */
export function useCreateCppFromUpload(appId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (
      input: CPPFromUploadIn,
    ): Promise<CPPFromUploadResponse> => {
      const formData = new FormData();
      formData.append("name", input.name);
      formData.append("locale", input.locale);
      formData.append("display_type", input.displayType);
      for (const file of input.files) {
        formData.append("files", file, file.name);
      }
      const response = await api.post<CPPFromUploadResponse>(
        `/apps/${appId}/cpps/from-upload`,
        formData,
        { headers: { "Content-Type": "multipart/form-data" } },
      );
      return response.data;
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: cppQueryKeys.cpps(appId) });
      notifications.show({
        title: "Custom Product Page created",
        message: `"${result.name ?? result.cpp_id}" created with ${result.uploaded_count} screenshot${
          result.uploaded_count === 1 ? "" : "s"
        }. You can now attach it to an Apple Search Ads ad group.`,
        color: "green",
      });
    },
    onError: (error: unknown) => {
      notifications.show({
        title: "Create failed",
        message: errorDetail(
          error,
          "Could not create the Custom Product Page from upload.",
        ),
        color: "red",
      });
    },
  });
}

// ---- Compare image (binary PNG -> object URL) ----

/**
 * Fetch the composited BEFORE/AFTER montage for a (cpp, locale, displayType)
 * triple and return it as an object URL ready to drop into ``<img src>``.
 *
 * The query is disabled until all three selectors are present so the page
 * can render its pickers before any request fires. The returned object URL
 * is revoked when the underlying blob changes or the component unmounts to
 * avoid leaking blob handles.
 */
export function useCompareImage(
  appId: number,
  cppId: string | null,
  locale: string | null,
  displayType: string | null,
) {
  const enabled =
    appId > 0 && !!cppId && !!locale && !!displayType;

  const query = useQuery({
    queryKey: cppQueryKeys.compare(
      appId,
      cppId ?? "",
      locale ?? "",
      displayType ?? "",
    ),
    queryFn: async (): Promise<string> => {
      const response = await api.get<Blob>(
        `/apps/${appId}/screenshots/compare`,
        {
          params: { cpp_id: cppId, locale, display_type: displayType },
          responseType: "blob",
        },
      );
      return URL.createObjectURL(response.data);
    },
    enabled,
    // The montage is expensive to build (downloads + composites images),
    // so keep it around while the user toggles back and forth.
    staleTime: 5 * 60_000,
    retry: false,
  });

  // Revoke the object URL when it is replaced or the consumer unmounts.
  useEffect(() => {
    const url = query.data;
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [query.data]);

  return query;
}

// ---- Live default-page screenshots (JSON) ----

/**
 * Fetch the live DEFAULT product page's screenshots for a (locale,
 * displayType) pair — the "before (current)" side of the in-browser compare.
 *
 * Returns a slot-ordered list of ``{slot, source_url, file_name}``. The
 * backend returns an empty list (not an error) when App Store Connect has no
 * live version/localization or no usable credentials, so the page can fall
 * back to a manual "before" upload. ``source_url`` points at Apple's public
 * CDN render, so it can be used directly in ``<img src>``.
 *
 * Disabled until both selectors are present so the page renders its pickers
 * before any request fires.
 */
export function useDefaultScreenshots(
  appId: number,
  locale: string | null,
  displayType: string | null,
) {
  const enabled = appId > 0 && !!locale && !!displayType;

  return useQuery({
    queryKey: cppQueryKeys.defaultScreenshots(
      appId,
      locale ?? "",
      displayType ?? "",
    ),
    queryFn: async (): Promise<DefaultScreenshot[]> => {
      const response = await api.get<DefaultScreenshotsResponse>(
        `/apps/${appId}/screenshots/default`,
        { params: { locale, display_type: displayType } },
      );
      return response.data.items;
    },
    enabled,
    // The live default page rarely changes between toggles; keep it warm.
    staleTime: 5 * 60_000,
    retry: false,
  });
}
