import { ConfirmModal, ConfirmModalProps } from "../ConfirmModal";
import { useEffect, useRef, useState } from "react";
import {
  AnnotationApiError, confirmAction, createAction, createInteraction,
  createReviewCoverage, deleteAction, getActionWorkspace, getClip, newOperationId,
  restoreAction, updateAction,
} from "./api";
import { ActionEditor } from "./ActionEditor";
import { AssistedReviewPanel } from "./AssistedReviewPanel";
import { ActionTimeline } from "./ActionTimeline";
import { ReviewPanel, type ReviewCoverageDraft } from "./ReviewPanel";
import { emptyDraft, validateActionDraft, type ActionDraft } from "./actionRules";
import type { ActionAnnotationCreate, ActionAnnotationUpdate, ActionAnnotationView, ActionWorkspaceView, AssistanceSuggestionView, ClipView } from "./types.generated";

export function ActionWorkspace({ clip, index, onIndex, frameReady, reviewOnly = false, onClipRevision, onClipReload, onDirtyChange }: {
  clip: ClipView;
  index: number;
  onIndex: (index: number) => void;
  frameReady: boolean;
  reviewOnly?: boolean;
  onClipRevision: (revision: number) => void;
  onClipReload: (clip: ClipView) => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [workspace, setWorkspace] = useState<ActionWorkspaceView | null>(null);
  const [selectedInteraction, setSelectedInteraction] = useState<string | null>(null);
  const [hand, setHand] = useState<"left" | "right" | "unknown">("unknown");
  const [draft, setDraft] = useState<ActionDraft>(emptyDraft);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTouched, setDraftTouched] = useState(false);
  const [confirmModal, setConfirmModal] = useState<ConfirmModalProps>({
    isOpen: false,
    title: "",
    message: "",
    confirmText: "Xác nhận",
    cancelText: "Hủy bỏ",
    kind: "primary",
    onConfirm: () => {},
    onCancel: () => {},
  });

  const askConfirm = (options: {
    title: string;
    message: string;
    confirmText?: string;
    cancelText?: string;
    kind?: "danger" | "primary";
    onConfirm: () => void;
  }) => {
    setConfirmModal({
      isOpen: true,
      title: options.title,
      message: options.message,
      confirmText: options.confirmText ?? "Xác nhận",
      cancelText: options.cancelText ?? "Hủy bỏ",
      kind: options.kind ?? "primary",
      onConfirm: () => {
        setConfirmModal((prev) => ({ ...prev, isOpen: false }));
        options.onConfirm();
      },
      onCancel: () => {
        setConfirmModal((prev) => ({ ...prev, isOpen: false }));
      },
    });
  };

  const [coverageDirty, setCoverageDirty] = useState(false);
  const [coverageFrameReset, setCoverageFrameReset] = useState(0);
  const [assistanceRefresh, setAssistanceRefresh] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operation = useRef<{ signature: string; id: string } | null>(null);
  const retryOperations = useRef(new Map<string, { signature: string; id: string }>());
  const currentClipId = useRef(clip.id);
  const mounted = useRef(true);

  currentClipId.current = clip.id;
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const accept = (next: ActionWorkspaceView) => {
    if (!mounted.current || next.clip_id !== currentClipId.current) return false;
    setWorkspace((current) => !current || next.clip_revision >= current.clip_revision ? next : current);
    onClipRevision(next.clip_revision);
    return true;
  };
  const adoptWorkspace = async (next: ActionWorkspaceView) => {
    if (!mounted.current || next.clip_id !== currentClipId.current) return { accepted: false, roiChanged: false };
    const roiChanged = next.roi_revision_id !== clip.roi?.id;
    let latestClip: ClipView | null = null;
    if (roiChanged) {
      latestClip = await getClip(clip.id);
      if (latestClip.id !== currentClipId.current || latestClip.roi?.id !== next.roi_revision_id) {
        throw new Error("ROI revision mismatch");
      }
    }
    if (!accept(next)) return { accepted: false, roiChanged };
    if (latestClip) {
      onClipReload(latestClip);
      setDraft((current) => ({
        ...current,
        start_frame: null,
        end_frame: null,
        crossing_frame: null,
      }));
      setCoverageFrameReset((current) => current + 1);
      operation.current = null;
    }
    return { accepted: true, roiChanged };
  };
  useEffect(() => {
    const controller = new AbortController();
    setWorkspace(null);
    setError(null);
    getActionWorkspace(clip.id, controller.signal)
      .then(async (next) => {
        const adopted = await adoptWorkspace(next);
        if (!adopted.accepted) return;
        const first = next.interactions[0]?.id ?? null;
        setSelectedInteraction(first);
        setDraft({ ...emptyDraft(), interaction_id: first });
        setEditingId(null);
        setDraftTouched(false);
      })
      .catch(() => { if (!controller.signal.aborted && mounted.current) setError("Không thể tải nhãn của clip này."); });
    return () => controller.abort();
  }, [clip.id]);
  useEffect(() => onDirtyChange(draftTouched || coverageDirty), [draftTouched, coverageDirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  const changeDraft = (next: ActionDraft) => {
    operation.current = null;
    setDraft(next);
    setDraftTouched(true);
  };
  const cancel = () => {
    operation.current = null;
    setEditingId(null);
    setDraft({ ...emptyDraft(), interaction_id: selectedInteraction });
    setDraftTouched(false);
    setError(null);
  };
  const applySuggestion = (item: AssistanceSuggestionView) => {
    onIndex(item.view_start_frame);
    setEditingId(null);
    setSelectedInteraction(null);
    setDraft({
      interaction_id: null,
      label: item.label,
      start_frame: item.action_start_frame ?? null,
      end_frame: item.action_end_frame ?? null,
      crossing_frame: item.crossing_estimate ?? null,
      object_kind: "unknown",
      visibility: "clear",
      uncertain_labels: [],
      unclear_reason: null,
      suggestion_id: item.id,
      estimated: true,
    });
    setDraftTouched(true);
    operation.current = null;
  };

  const useSuggestion = (item: AssistanceSuggestionView) => {
    if (saving) return;
    if (draftTouched) {
      askConfirm({
        title: "Bỏ nhãn chưa lưu?",
        message: "Nhãn đang chỉnh sửa chưa lưu sẽ bị hủy bỏ để áp dụng gợi ý từ model AI.",
        confirmText: "Dùng gợi ý",
        kind: "danger",
        onConfirm: () => applySuggestion(item),
      });
      return;
    }
    applySuggestion(item);
  };
  const _unusedUseSuggestionBody = (item: AssistanceSuggestionView) => {
    setEditingId(null);
    setSelectedInteraction(null);
    setDraft({
      interaction_id: null,
      label: item.label,
      start_frame: item.action_start_frame ?? null,
      end_frame: item.action_end_frame ?? null,
      crossing_frame: item.crossing_estimate ?? null,
      object_kind: "unknown",
      visibility: "clear",
      uncertain_labels: [],
      unclear_reason: null,
      suggestion_id: item.id,
      estimated: true,
    });
    setDraftTouched(true);
    setError(null);
    operation.current = null;
  };
  const reconcileConflict = async ({ roiMessage, staleMessage }: {
    roiMessage: string;
    staleMessage: string;
  }) => {
    const latest = await getActionWorkspace(clip.id);
    const adopted = await adoptWorkspace(latest);
    if (!adopted.accepted) return false;
    setError(adopted.roiChanged ? roiMessage : staleMessage);
    return adopted.roiChanged;
  };
  const addInteraction = async () => {
    if (!workspace || saving) return;
    const signature = JSON.stringify({
      expected_clip_revision: workspace.clip_revision, hand,
    });
    const key = "create-interaction";
    if (retryOperations.current.get(key)?.signature !== signature) {
      retryOperations.current.set(key, { signature, id: newOperationId() });
    }
    const operationId = retryOperations.current.get(key)!.id;
    setSaving(true);
    setError(null);
    try {
      const next = await createInteraction(clip.id, {
        operation_id: operationId, expected_clip_revision: workspace.clip_revision, hand,
      });
      if (!accept(next)) return;
      retryOperations.current.delete(key);
      const created = next.interactions[next.interactions.length - 1];
      setSelectedInteraction(created.id);
      setDraft((current) => ({ ...current, interaction_id: created.id }));
    } catch { if (mounted.current) setError("Không thể tạo lượt tay. Có thể thử lại mà không tạo trùng."); }
    finally { if (mounted.current) setSaving(false); }
  };
  const save = async (draftToSave = draft) => {
    if (!workspace) return;
    const validation = validateActionDraft(draftToSave);
    if (validation) { setError(validation); return; }
    const fields = {
      interaction_id: draftToSave.interaction_id,
      label: draftToSave.label!,
      start_frame: draftToSave.start_frame!,
      end_frame: draftToSave.end_frame!,
      crossing_frame: draftToSave.crossing_frame,
      object_kind: draftToSave.object_kind,
      visibility: draftToSave.visibility,
      uncertain_labels: draftToSave.label === "unclear" ? draftToSave.uncertain_labels : [],
      unclear_reason: draftToSave.label === "unclear" ? draftToSave.unclear_reason : null,
    };
    const edited = editingId
      ? workspace.annotations.find((item) => item.id === editingId) ?? null
      : null;
    const version = edited ? { expected_annotation_revision: edited.revision } : {};
    const signature = JSON.stringify({ ...fields, ...version, expected_clip_revision: workspace.clip_revision });
    if (operation.current?.signature !== signature) operation.current = { signature, id: newOperationId() };
    const base = {
      ...fields,
      operation_id: operation.current.id,
      expected_clip_revision: workspace.clip_revision,
    };
    setSaving(true);
    setError(null);
    try {
      const next = edited
        ? await updateAction(clip.id, edited.id, {
          ...base, expected_annotation_revision: edited.revision,
        } satisfies ActionAnnotationUpdate)
        : await createAction(clip.id, {
          ...base, suggestion_id: draftToSave.suggestion_id,
        } satisfies ActionAnnotationCreate);
      if (accept(next) && operation.current?.signature === signature) {
        if (!edited && draftToSave.suggestion_id) setAssistanceRefresh((value) => value + 1);
        cancel();
      }
    } catch (reason) {
      if (reason instanceof AnnotationApiError && reason.status === 409) {
        if (reason.conflictingAnnotationId) {
          setError(`Nhãn chồng với event ${reason.conflictingAnnotationId}. Hãy chia hoặc sửa khoảng.`);
        } else {
          try {
            await reconcileConflict({
              roiMessage: "ROI đã thay đổi ở tab khác. Nháp còn giữ nhưng cần xem ROI mới và đánh dấu lại các frame.",
              staleMessage: "Đã tải bản mới từ server. Nháp vẫn được giữ; hãy kiểm tra rồi lưu lại.",
            });
            operation.current = null;
          } catch {
            setError("Dữ liệu đã thay đổi ở tab khác. Nháp vẫn được giữ; chưa thể tải bản server.");
          }
        }
      } else setError("Không thể lưu nhãn. Nháp vẫn được giữ.");
    } finally { if (mounted.current) setSaving(false); }
  };
  const doSelect = (annotation: ActionAnnotationView) => {
    onIndex(annotation.start_frame);
    if (reviewOnly || annotation.deleted) return;
    setEditingId(annotation.id);
    setSelectedInteraction(annotation.interaction_id);
    setDraft({
      interaction_id: annotation.interaction_id,
      label: annotation.label,
      start_frame: annotation.start_frame,
      end_frame: annotation.end_frame,
      crossing_frame: annotation.crossing_frame ?? null,
      object_kind: annotation.object_kind,
      visibility: annotation.visibility,
      uncertain_labels: annotation.uncertain_labels ?? [],
      unclear_reason: annotation.unclear_reason ?? null,
      suggestion_id: null,
      estimated: false,
    });
    setDraftTouched(false);
    operation.current = null;
  };

  const select = (annotation: ActionAnnotationView) => {
    if (saving) return;
    if (draftTouched) {
      askConfirm({
        title: "Bỏ nhãn chưa lưu?",
        message: "Nhãn đang chỉnh sửa chưa lưu sẽ bị hủy bỏ nếu mở sự kiện khác.",
        confirmText: "Mở sự kiện",
        kind: "danger",
        onConfirm: () => doSelect(annotation),
      });
      return;
    }
    doSelect(annotation);
  };
  const _unusedSelectBody = (annotation: ActionAnnotationView) => {
    if (reviewOnly || annotation.deleted) return;
    setEditingId(annotation.id);
    setSelectedInteraction(annotation.interaction_id);
    setDraft({
      interaction_id: annotation.interaction_id,
      label: annotation.label,
      start_frame: annotation.start_frame,
      end_frame: annotation.end_frame,
      crossing_frame: annotation.crossing_frame ?? null,
      object_kind: annotation.object_kind,
      visibility: annotation.visibility,
      uncertain_labels: annotation.uncertain_labels ?? [],
      unclear_reason: annotation.unclear_reason ?? null,
      suggestion_id: null,
      estimated: false,
    });
    setDraftTouched(false);
    operation.current = null;
  };
  const mutate = async (kind: "confirm" | "delete" | "restore", annotation: ActionAnnotationView) => {
    if (!workspace || saving) return;
    if (editingId === annotation.id && draftTouched) {
      askConfirm({
        title: "Bỏ thay đổi đang sửa?",
        message: "Nhãn đang chỉnh sửa chưa lưu sẽ bị hủy bỏ khi thực hiện thao tác này.",
        confirmText: "Tiếp tục",
        kind: "danger",
        onConfirm: () => void executeMutate(kind, annotation),
      });
      return;
    }
    await executeMutate(kind, annotation);
  };

  const executeMutate = async (kind: "confirm" | "delete" | "restore", annotation: ActionAnnotationView) => {
    if (!workspace) return;
    setError(null);
    const signature = JSON.stringify({
      expected_clip_revision: workspace.clip_revision,
      expected_annotation_revision: annotation.revision,
    });
    const key = `${kind}:${annotation.id}`;
    if (retryOperations.current.get(key)?.signature !== signature) {
      retryOperations.current.set(key, { signature, id: newOperationId() });
    }
    const body = {
      operation_id: retryOperations.current.get(key)!.id,
      expected_clip_revision: workspace.clip_revision,
      expected_annotation_revision: annotation.revision,
    };
    setSaving(true);
    try {
      const next = kind === "confirm" ? await confirmAction(clip.id, annotation.id, body)
        : kind === "delete" ? await deleteAction(clip.id, annotation.id, body)
          : await restoreAction(clip.id, annotation.id, body);
      if (!accept(next)) return;
      retryOperations.current.delete(key);
      if (editingId === annotation.id) cancel();
    } catch (reason) {
      if (reason instanceof AnnotationApiError && reason.status === 409) {
        try {
          await reconcileConflict({
            roiMessage: "ROI đã thay đổi ở tab khác. Hãy xem ROI mới trước khi tiếp tục kiểm tra.",
            staleMessage: "Đã tải trạng thái mới từ server. Nháp đang sửa vẫn được giữ.",
          });
          retryOperations.current.delete(key);
        } catch { setError("Dữ liệu đã thay đổi; chưa thể tải trạng thái mới."); }
      } else setError("Không thể cập nhật trạng thái. Có thể thử lại mà không tạo thao tác trùng.");
    } finally { if (mounted.current) setSaving(false); }
  };
  const recordCoverage = async (coverage: ReviewCoverageDraft) => {
    if (!workspace || saving) return false;
    const signature = JSON.stringify({ ...coverage, expected_clip_revision: workspace.clip_revision });
    const key = "create-coverage";
    if (retryOperations.current.get(key)?.signature !== signature) {
      retryOperations.current.set(key, { signature, id: newOperationId() });
    }
    setSaving(true);
    setError(null);
    try {
      const next = await createReviewCoverage(clip.id, {
        ...coverage,
        operation_id: retryOperations.current.get(key)!.id,
        expected_clip_revision: workspace.clip_revision,
      });
      if (!accept(next)) return false;
      retryOperations.current.delete(key);
      setCoverageDirty(false);
      return true;
    } catch (reason) {
      if (reason instanceof AnnotationApiError && reason.status === 409) {
        try {
          await reconcileConflict({
            roiMessage: "ROI đã thay đổi ở tab khác. Các lớp đã chọn còn giữ; hãy xem lại và đặt lại khoảng frame.",
            staleMessage: "Đã tải trạng thái mới từ server. Khoảng kiểm tra vẫn được giữ để bạn xem lại.",
          });
          retryOperations.current.delete(key);
        } catch { setError("Dữ liệu đã thay đổi; chưa thể tải trạng thái mới. Khoảng kiểm tra vẫn được giữ."); }
      } else setError("Không thể ghi coverage. Có thể thử lại mà không tạo bản ghi trùng.");
      return false;
    } finally { if (mounted.current) setSaving(false); }
  };

  if (!workspace) return <div className="action-workspace">{error ? <p className="error" role="alert">{error}</p> : <p>Đang tải nhãn…</p>}</div>;
  return <div className={`action-workspace ${reviewOnly ? "review-mode" : ""}`}>
    {!reviewOnly && <aside className="interaction-panel"><div className="interaction-heading"><h3>Lượt tay</h3><span>Không bắt buộc tracking</span></div>
      <div className="interaction-list"><button type="button" disabled={saving} aria-pressed={selectedInteraction === null} onClick={() => { setSelectedInteraction(null); changeDraft({ ...draft, interaction_id: null }); }}>Toàn ROI<small>không rõ lượt · chỉ dùng Unclear</small></button>{workspace.interactions.map((item, position) => <button type="button" disabled={saving} aria-pressed={selectedInteraction === item.id} key={item.id} onClick={() => { setSelectedInteraction(item.id); changeDraft({ ...draft, interaction_id: item.id }); }}>Lượt {position + 1}<small>{item.hand === "left" ? "tay trái" : item.hand === "right" ? "tay phải" : "chưa rõ tay"}</small></button>)}</div>
      <div className="new-interaction"><select aria-label="Tay của lượt mới" value={hand} onChange={(event) => setHand(event.target.value as typeof hand)}><option value="unknown">Chưa rõ tay</option><option value="left">Tay trái</option><option value="right">Tay phải</option></select><button type="button" onClick={() => void addInteraction()} disabled={saving}>+ Tạo lượt</button></div>
    </aside>}
    <div className="action-content">
      {reviewOnly && <div className="review-summary"><h3>Kiểm tra event</h3><p>{workspace.annotations.filter((item) => !item.deleted && item.review_state !== "confirmed").length} event đang chờ xác nhận. Chưa review không được xem là không có hành động.</p></div>}
      {reviewOnly && <ReviewPanel key={coverageFrameReset} coverage={workspace.review_coverage} currentFrame={index} frameReady={frameReady} busy={saving} error={error} resetExactFrames={coverageFrameReset} onRecord={recordCoverage} onDirtyChange={setCoverageDirty} />}
      {!reviewOnly && <AssistedReviewPanel key={`${clip.id}:${assistanceRefresh}`} clipId={clip.id} clipRevision={workspace.clip_revision} frameCount={clip.media!.frame_count} currentFrame={index} onSeek={onIndex} onUseSuggestion={useSuggestion} onQueueChanged={() => undefined} />}
      {!reviewOnly && <ActionEditor draft={draft} onChange={changeDraft} currentFrame={index} frameReady={frameReady} saving={saving} editing={Boolean(editingId)} error={error} onSave={(nextDraft) => void save(nextDraft)} onCancel={cancel} />}
      <ActionTimeline annotations={workspace.annotations} frameCount={clip.media!.frame_count} busy={saving} onSelect={select} onConfirm={(item) => void mutate("confirm", item)} onDelete={(item) => void mutate("delete", item)} onRestore={(item) => void mutate("restore", item)} />
    </div>
    <ConfirmModal {...confirmModal} />
  </div>;
}
