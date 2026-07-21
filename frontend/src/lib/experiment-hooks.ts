/**
 * TanStack Query hooks for Product Page Optimization (PPO) — App Store Version
 * Experiments.
 *
 * Kept in a dedicated module (like ``cpp-hooks.ts``) because these are the only
 * consumers of the experiment REST surface added for the Experiments page. They
 * follow the same conventions as ``hooks.ts``: every call goes through the
 * shared ``api`` axios client (JWT attached by its request interceptor), query
 * keys are namespaced, and mutations emit Mantine notifications.
 *
 * Apple exposes no experiment *results* via the API (impressions, conversion,
 * confidence live only in the ASC Analytics UI), so there is no results hook —
 * the page deep-links to App Store Connect for those.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { notifications } from "@mantine/notifications";
import api from "@/lib/api";

// ---- Types ----

export interface Experiment {
  id: string;
  name: string | null;
  platform: string | null;
  traffic_proportion: number | null;
  state: string | null;
  start_date: string | null;
  end_date: string | null;
  review_required: boolean | null;
}

export interface ExperimentListResponse {
  items: Experiment[];
}

export interface ExperimentCreateIn {
  name: string;
  traffic_proportion: number;
  platform?: string;
}

export interface ExperimentUpdateIn {
  name?: string;
  traffic_proportion?: number;
  state?: string;
}

export interface Treatment {
  id: string;
  name: string | null;
  app_icon_name: string | null;
  promoted_date: string | null;
}

export interface TreatmentListResponse {
  items: Treatment[];
}

export interface TreatmentCreateIn {
  name: string;
  app_icon_name?: string | null;
}

export interface TreatmentFromUploadResponse {
  treatment_id: string;
  localization_id: string;
  locale: string;
  uploaded_count: number;
}

export interface TreatmentFromUploadIn {
  treatmentId: string;
  locale: string;
  displayType: string;
  files: File[];
}

// ---- Error helpers ----

/**
 * Pull the server-supplied ``detail`` off an axios error body, falling back to
 * ``fallback`` when the shape doesn't match. Used by the mutation ``onError``
 * notifications below.
 */
function errorDetail(error: unknown, fallback: string): string {
  return (
    (error as { response?: { data?: { detail?: string } } })?.response?.data
      ?.detail ?? fallback
  );
}

// ---- Query keys ----

export const experimentQueryKeys = {
  experiments: (appId: number) => ["experiments", appId] as const,
  treatments: (appId: number, experimentId: string) =>
    ["experiment-treatments", appId, experimentId] as const,
};

// ---- Experiments ----

export function useExperiments(appId: number) {
  return useQuery({
    queryKey: experimentQueryKeys.experiments(appId),
    queryFn: async (): Promise<Experiment[]> => {
      const response = await api.get<ExperimentListResponse>(
        `/apps/${appId}/experiments`,
      );
      return response.data.items;
    },
    enabled: appId > 0,
  });
}

export function useCreateExperiment(appId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: ExperimentCreateIn): Promise<Experiment> => {
      const response = await api.post<Experiment>(
        `/apps/${appId}/experiments`,
        body,
      );
      return response.data;
    },
    onSuccess: (experiment) => {
      queryClient.invalidateQueries({
        queryKey: experimentQueryKeys.experiments(appId),
      });
      notifications.show({
        title: "Experiment created",
        message: `"${experiment.name ?? experiment.id}" created. Add up to 3 treatments, then submit for review.`,
        color: "green",
      });
    },
    onError: (error: unknown) => {
      notifications.show({
        title: "Create failed",
        message: errorDetail(
          error,
          "Could not create the experiment. Apple allows only one draft experiment per app at a time.",
        ),
        color: "red",
      });
    },
  });
}

export function useUpdateExperiment(appId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      experimentId: string;
      body: ExperimentUpdateIn;
    }): Promise<Experiment> => {
      const response = await api.patch<Experiment>(
        `/apps/${appId}/experiments/${input.experimentId}`,
        input.body,
      );
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: experimentQueryKeys.experiments(appId),
      });
    },
    onError: (error: unknown) => {
      notifications.show({
        title: "Update failed",
        message: errorDetail(error, "Could not update the experiment."),
        color: "red",
      });
    },
  });
}

export function useDeleteExperiment(appId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (experimentId: string): Promise<void> => {
      await api.delete(`/apps/${appId}/experiments/${experimentId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: experimentQueryKeys.experiments(appId),
      });
      notifications.show({
        title: "Experiment deleted",
        message: "The experiment was removed.",
        color: "green",
      });
    },
    onError: (error: unknown) => {
      notifications.show({
        title: "Delete failed",
        message: errorDetail(
          error,
          "Could not delete the experiment (running experiments cannot be deleted — stop it first).",
        ),
        color: "red",
      });
    },
  });
}

// ---- Treatments ----

export function useTreatments(appId: number, experimentId: string | null) {
  return useQuery({
    queryKey: experimentQueryKeys.treatments(appId, experimentId ?? ""),
    queryFn: async (): Promise<Treatment[]> => {
      const response = await api.get<TreatmentListResponse>(
        `/apps/${appId}/experiments/${experimentId}/treatments`,
      );
      return response.data.items;
    },
    enabled: appId > 0 && !!experimentId,
  });
}

export function useCreateTreatment(appId: number, experimentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: TreatmentCreateIn): Promise<Treatment> => {
      const response = await api.post<Treatment>(
        `/apps/${appId}/experiments/${experimentId}/treatments`,
        body,
      );
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: experimentQueryKeys.treatments(appId, experimentId),
      });
      notifications.show({
        title: "Treatment added",
        message: "Add localized screenshots to the treatment below.",
        color: "green",
      });
    },
    onError: (error: unknown) => {
      notifications.show({
        title: "Add treatment failed",
        message: errorDetail(
          error,
          "Could not add the treatment (Apple allows at most 3 per experiment).",
        ),
        color: "red",
      });
    },
  });
}

export function useDeleteTreatment(appId: number, experimentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (treatmentId: string): Promise<void> => {
      await api.delete(
        `/apps/${appId}/experiments/${experimentId}/treatments/${treatmentId}`,
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: experimentQueryKeys.treatments(appId, experimentId),
      });
      notifications.show({
        title: "Treatment deleted",
        message: "The treatment was removed.",
        color: "green",
      });
    },
    onError: (error: unknown) => {
      notifications.show({
        title: "Delete failed",
        message: errorDetail(error, "Could not delete the treatment."),
        color: "red",
      });
    },
  });
}

// ---- Treatment screenshots (from-upload) ----

/**
 * Upload an "after" screenshot set into a treatment localization. Posts the
 * File[] (plus locale + display type) as multipart/form-data to the
 * ``.../treatments/{id}/from-upload`` endpoint, which ensures the localization
 * and uploads each screenshot to App Store Connect. Invalidates the treatment
 * list so freshly-populated treatments reflect their new state.
 */
export function useUploadTreatmentScreenshots(
  appId: number,
  experimentId: string,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (
      input: TreatmentFromUploadIn,
    ): Promise<TreatmentFromUploadResponse> => {
      const formData = new FormData();
      formData.append("locale", input.locale);
      formData.append("display_type", input.displayType);
      for (const file of input.files) {
        formData.append("files", file, file.name);
      }
      const response = await api.post<TreatmentFromUploadResponse>(
        `/apps/${appId}/experiments/${experimentId}/treatments/${input.treatmentId}/from-upload`,
        formData,
        { headers: { "Content-Type": "multipart/form-data" } },
      );
      return response.data;
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({
        queryKey: experimentQueryKeys.treatments(appId, experimentId),
      });
      notifications.show({
        title: "Screenshots uploaded",
        message: `Uploaded ${result.uploaded_count} screenshot${
          result.uploaded_count === 1 ? "" : "s"
        } to the treatment (${result.locale}).`,
        color: "green",
      });
    },
    onError: (error: unknown) => {
      notifications.show({
        title: "Upload failed",
        message: errorDetail(
          error,
          "Could not upload the treatment screenshots.",
        ),
        color: "red",
      });
    },
  });
}
