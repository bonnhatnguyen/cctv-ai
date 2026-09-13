import type {
  ActionAnnotationCreate,
  ActionAnnotationUpdate,
  ActionMutation,
  ActionWorkspaceView,
  AssistanceModelInfo,
  AssistanceRunCancel,
  AssistanceRunCreate,
  AssistanceRunListView,
  AssistanceRunView,
  AssistanceSuggestionListView,
  AssistanceSuggestionView,
  CameraSetupCreate,
  CameraSetupListView,
  CameraSetupView,
  ClipListView,
  ClipView,
  InteractionCreate,
  InteractionUpdate,
  RegisterClip,
  ReleasePreparedMedia,
  ReviewCoverageWrite,
  RetryPreparation,
  RoiWrite,
  StorageView,
  SuggestionReject,
  TemplateWrite,
} from "./types.generated";

export class AnnotationApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    public readonly conflictingAnnotationId: string | null = null,
  ) {
    super(code);
    this.name = "AnnotationApiError";
  }
}

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let code = "annotation_request_failed";
    let conflictingAnnotationId: string | null = null;
    try {
      const body = await response.json() as { detail?: unknown };
      if (typeof body.detail === "string") code = body.detail;
      if (body.detail && typeof body.detail === "object") {
        const detail = body.detail as Record<string, unknown>;
        if (typeof detail.code === "string") code = detail.code;
        if (typeof detail.conflicting_annotation_id === "string") {
          conflictingAnnotationId = detail.conflicting_annotation_id;
        }
      }
    } catch { /* use stable fallback */ }
    throw new AnnotationApiError(response.status, code, conflictingAnnotationId);
  }
  return response.json() as Promise<T>;
}

const jsonInit = (method: string, body: unknown, signal?: AbortSignal): RequestInit => ({
  method,
  signal,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const newOperationId = () => crypto.randomUUID();
export const registerClip = (body: RegisterClip, signal?: AbortSignal) =>
  request<ClipView>("/api/v2/annotations/clips", jsonInit("POST", body, signal));
export const getClip = (id: string, signal?: AbortSignal) =>
  request<ClipView>(`/api/v2/annotations/clips/${encodeURIComponent(id)}`, { signal });
export const listClips = (cursor?: string | null, signal?: AbortSignal) =>
  request<ClipListView>(`/api/v2/annotations/clips${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`, { signal });
export const retryClip = (id: string, body: RetryPreparation, signal?: AbortSignal) =>
  request<ClipView>(`/api/v2/annotations/clips/${encodeURIComponent(id)}/retry`, jsonInit("POST", body, signal));
export const releaseClip = (id: string, body: ReleasePreparedMedia, signal?: AbortSignal) =>
  request<ClipView>(`/api/v2/annotations/clips/${encodeURIComponent(id)}/release`, jsonInit("POST", body, signal));
export const getStorage = (signal?: AbortSignal) => request<StorageView>("/api/v2/annotations/storage", { signal });
export const listSetups = (signal?: AbortSignal) => request<CameraSetupListView>("/api/v2/annotations/setups", { signal });
export const createSetup = (body: CameraSetupCreate, signal?: AbortSignal) =>
  request<CameraSetupView>("/api/v2/annotations/setups", jsonInit("POST", body, signal));
export const saveTemplate = (id: string, body: TemplateWrite, signal?: AbortSignal) =>
  request<CameraSetupView>(`/api/v2/annotations/setups/${encodeURIComponent(id)}/template`, jsonInit("PUT", body, signal));
export const saveRoi = (id: string, body: RoiWrite, signal?: AbortSignal) =>
  request<ClipView>(`/api/v2/annotations/clips/${encodeURIComponent(id)}/roi`, jsonInit("PUT", body, signal));

const clipActionsUrl = (clipId: string) =>
  `/api/v2/annotations/clips/${encodeURIComponent(clipId)}/actions`;

export const getActionWorkspace = (clipId: string, signal?: AbortSignal) =>
  request<ActionWorkspaceView>(clipActionsUrl(clipId), { signal });
export const createInteraction = (
  clipId: string, body: InteractionCreate, signal?: AbortSignal,
) => request<ActionWorkspaceView>(
  `/api/v2/annotations/clips/${encodeURIComponent(clipId)}/interactions`,
  jsonInit("POST", body, signal),
);
export const updateInteraction = (
  clipId: string, interactionId: string, body: InteractionUpdate, signal?: AbortSignal,
) => request<ActionWorkspaceView>(
  `/api/v2/annotations/clips/${encodeURIComponent(clipId)}/interactions/${encodeURIComponent(interactionId)}`,
  jsonInit("PUT", body, signal),
);
export const createAction = (
  clipId: string, body: ActionAnnotationCreate, signal?: AbortSignal,
) => request<ActionWorkspaceView>(clipActionsUrl(clipId), jsonInit("POST", body, signal));
export const updateAction = (
  clipId: string, annotationId: string, body: ActionAnnotationUpdate, signal?: AbortSignal,
) => request<ActionWorkspaceView>(
  `${clipActionsUrl(clipId)}/${encodeURIComponent(annotationId)}`,
  jsonInit("PUT", body, signal),
);
const mutateAction = (
  action: "confirm" | "delete" | "restore",
  clipId: string,
  annotationId: string,
  body: ActionMutation,
  signal?: AbortSignal,
) => request<ActionWorkspaceView>(
  `${clipActionsUrl(clipId)}/${encodeURIComponent(annotationId)}/${action}`,
  jsonInit("POST", body, signal),
);
export const confirmAction = (
  clipId: string, annotationId: string, body: ActionMutation, signal?: AbortSignal,
) => mutateAction("confirm", clipId, annotationId, body, signal);
export const deleteAction = (
  clipId: string, annotationId: string, body: ActionMutation, signal?: AbortSignal,
) => mutateAction("delete", clipId, annotationId, body, signal);
export const restoreAction = (
  clipId: string, annotationId: string, body: ActionMutation, signal?: AbortSignal,
) => mutateAction("restore", clipId, annotationId, body, signal);
export const createReviewCoverage = (
  clipId: string, body: ReviewCoverageWrite, signal?: AbortSignal,
) => request<ActionWorkspaceView>(
  `/api/v2/annotations/clips/${encodeURIComponent(clipId)}/review-coverage`,
  jsonInit("POST", body, signal),
);

const assistanceBase = (clipId: string) =>
  `/api/v2/annotations/clips/${encodeURIComponent(clipId)}`;

export const listAssistanceModels = (signal?: AbortSignal) =>
  request<AssistanceModelInfo[]>("/api/v2/annotations/assist-models", { signal });
export const startAssistanceRun = (
  clipId: string, body: AssistanceRunCreate, signal?: AbortSignal,
) => request<AssistanceRunView>(
  `${assistanceBase(clipId)}/assist-runs`, jsonInit("POST", body, signal),
);
export const listAssistanceRuns = (
  clipId: string, activeOnly = false, signal?: AbortSignal,
) => request<AssistanceRunListView>(
  `${assistanceBase(clipId)}/assist-runs${activeOnly ? "?active_only=true" : ""}`,
  { signal },
);
export const getAssistanceRun = (
  clipId: string, runId: string, signal?: AbortSignal,
) => request<AssistanceRunView>(
  `${assistanceBase(clipId)}/assist-runs/${encodeURIComponent(runId)}`, { signal },
);
export const cancelAssistanceRun = (
  clipId: string, runId: string, body: AssistanceRunCancel, signal?: AbortSignal,
) => request<AssistanceRunView>(
  `${assistanceBase(clipId)}/assist-runs/${encodeURIComponent(runId)}/cancel`,
  jsonInit("POST", body, signal),
);
export const listAssistanceSuggestions = (
  clipId: string, state = "pending", signal?: AbortSignal,
) => request<AssistanceSuggestionListView>(
  `${assistanceBase(clipId)}/assist-suggestions?state=${encodeURIComponent(state)}`,
  { signal },
);
export const rejectAssistanceSuggestion = (
  clipId: string, suggestionId: string, body: SuggestionReject,
  signal?: AbortSignal,
) => request<AssistanceSuggestionView>(
  `${assistanceBase(clipId)}/assist-suggestions/${encodeURIComponent(suggestionId)}/reject`,
  jsonInit("POST", body, signal),
);
