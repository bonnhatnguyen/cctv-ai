import type {
  CameraSetupCreate,
  CameraSetupListView,
  CameraSetupView,
  ClipListView,
  ClipView,
  RegisterClip,
  ReleasePreparedMedia,
  RetryPreparation,
  RoiWrite,
  StorageView,
  TemplateWrite,
} from "./types.generated";

export class AnnotationApiError extends Error {
  constructor(public readonly status: number, public readonly code: string) {
    super(code);
    this.name = "AnnotationApiError";
  }
}

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let code = "annotation_request_failed";
    try {
      const body = await response.json() as { detail?: unknown };
      if (typeof body.detail === "string") code = body.detail;
    } catch { /* use stable fallback */ }
    throw new AnnotationApiError(response.status, code);
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
