import { useEffect, useMemo, useRef, useState } from "react";
import {
  cancelAssistanceRun, getAssistanceRun, listAssistanceModels,
  listAssistanceRuns, listAssistanceSuggestions, newOperationId,
  rejectAssistanceSuggestion, startAssistanceRun,
} from "./api";
import type {
  AssistanceModelInfo, AssistanceRunView, AssistanceSuggestionView,
} from "./types.generated";

const MODEL_NAMES = { dino: "DINO", mediapipe: "MediaPipe" } as const;
const REASONS = {
  crossing: "qua biên ROI", boundary: "ở sát biên ROI", track_gap: "mất dấu tay",
  association: "chưa chắc cùng lượt tay", clip_boundary: "sát đầu hoặc cuối clip",
} as const;

export function AssistedReviewPanel({
  clipId, clipRevision, frameCount, currentFrame, onSeek, onUseSuggestion,
  onQueueChanged,
}: {
  clipId: string;
  clipRevision: number;
  frameCount: number;
  currentFrame: number;
  onSeek: (frame: number) => void;
  onUseSuggestion: (suggestion: AssistanceSuggestionView) => void;
  onQueueChanged: () => void;
}) {
  const [models, setModels] = useState<AssistanceModelInfo[]>([]);
  const [runs, setRuns] = useState<AssistanceRunView[]>([]);
  const [runCursor, setRunCursor] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<AssistanceSuggestionView[]>([]);
  const [suggestionCursor, setSuggestionCursor] = useState<string | null>(null);
  const [suggestionState, setSuggestionState] = useState<"pending" | "accepted" | "rejected" | "stale">("pending");
  const [model, setModel] = useState<"dino" | "mediapipe">("dino");
  const [startFrame, setStartFrame] = useState(currentFrame);
  const [endFrame, setEndFrame] = useState(Math.min(frameCount - 1, currentFrame + 250));
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loadingMoreSuggestions, setLoadingMoreSuggestions] = useState(false);
  const [loadingMoreRuns, setLoadingMoreRuns] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mountedClip = useRef(clipId);
  const startAttempt = useRef<{ key: string; operationId: string } | null>(null);
  const cancelAttempts = useRef(new Map<string, string>());
  const rejectAttempts = useRef(new Map<string, string>());
  const suggestionRequest = useRef(0);
  const runRequest = useRef(0);
  mountedClip.current = clipId;

  const reload = async (
    signal?: AbortSignal,
    state: typeof suggestionState = suggestionState,
  ) => {
    setLoadingMoreSuggestions(false);
    setLoadingMoreRuns(false);
    const suggestionGeneration = ++suggestionRequest.current;
    const runGeneration = ++runRequest.current;
    const [nextModels, nextRuns, activeRuns, nextSuggestions] = await Promise.all([
      listAssistanceModels(signal), listAssistanceRuns(clipId, false, signal, null, 5),
      listAssistanceRuns(clipId, true, signal),
      listAssistanceSuggestions(clipId, state, signal),
    ]);
    if (mountedClip.current !== clipId) return;
    setModels(nextModels);
    if (runGeneration === runRequest.current) {
      setRuns([
        ...activeRuns.items,
        ...nextRuns.items.filter((item) => !activeRuns.items.some((active) => active.id === item.id)),
      ]);
      setRunCursor(nextRuns.next_cursor);
    }
    if (suggestionGeneration === suggestionRequest.current) {
      setSuggestions(nextSuggestions.items);
      setSuggestionCursor(nextSuggestions.next_cursor);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setSuggestionState("pending");
    setSuggestionCursor(null);
    setRunCursor(null);
    setLoadingMoreSuggestions(false);
    setLoadingMoreRuns(false);
    startAttempt.current = null;
    cancelAttempts.current.clear();
    rejectAttempts.current.clear();
    setStartFrame(currentFrame);
    setEndFrame(Math.min(frameCount - 1, currentFrame + 250));
    reload(controller.signal, "pending")
      .catch(() => { if (!controller.signal.aborted) setError("Không thể tải phần model hỗ trợ."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [clipId]);

  const changeSuggestionState = async (state: typeof suggestionState) => {
    setSuggestionState(state);
    setLoadingMoreSuggestions(false);
    const request = ++suggestionRequest.current;
    try {
      const next = await listAssistanceSuggestions(clipId, state);
      if (request === suggestionRequest.current && mountedClip.current === clipId) {
        setSuggestions(next.items);
        setSuggestionCursor(next.next_cursor);
      }
    } catch {
      if (request === suggestionRequest.current) {
        setError("Không thể tải danh sách gợi ý.");
      }
    }
  };

  const loadMoreSuggestions = async () => {
    if (!suggestionCursor || loadingMoreSuggestions) return;
    const cursor = suggestionCursor;
    const state = suggestionState;
    const request = ++suggestionRequest.current;
    setLoadingMoreSuggestions(true);
    try {
      const next = await listAssistanceSuggestions(
        clipId, state, undefined, cursor,
      );
      if (request !== suggestionRequest.current || mountedClip.current !== clipId) return;
      setSuggestions((current) => [
        ...current,
        ...next.items.filter((item) => !current.some((existing) => existing.id === item.id)),
      ]);
      setSuggestionCursor(next.next_cursor);
    } catch {
      if (request === suggestionRequest.current) setError("Không thể tải thêm gợi ý.");
    } finally {
      if (request === suggestionRequest.current) setLoadingMoreSuggestions(false);
    }
  };

  const loadMoreRuns = async () => {
    if (!runCursor || loadingMoreRuns) return;
    const cursor = runCursor;
    const request = ++runRequest.current;
    setLoadingMoreRuns(true);
    try {
      const next = await listAssistanceRuns(clipId, false, undefined, cursor, 5);
      if (request !== runRequest.current || mountedClip.current !== clipId) return;
      setRuns((current) => [
        ...current,
        ...next.items.filter((item) => !current.some((existing) => existing.id === item.id)),
      ]);
      setRunCursor(next.next_cursor);
    } catch {
      if (request === runRequest.current) setError("Không thể tải thêm lịch sử model.");
    } finally {
      if (request === runRequest.current) setLoadingMoreRuns(false);
    }
  };

  const active = useMemo(
    () => runs.find((run) => run.status === "running")
      ?? [...runs].reverse().find((run) => run.status === "queued") ?? null,
    [runs],
  );
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    const controller = new AbortController();
    let delay = 1000;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await getAssistanceRun(clipId, active.id, controller.signal);
        if (stopped || mountedClip.current !== clipId) return;
        setRuns((current) => current.map((item) => item.id === next.id ? next : item));
        if (next.status === "queued" || next.status === "running") {
          delay = Math.min(delay * 2, 4000);
          timer = setTimeout(poll, delay);
        } else {
          await reload(controller.signal);
          onQueueChanged();
        }
      } catch {
        if (!controller.signal.aborted) setError("Mất kết nối khi theo dõi model.");
      }
    };
    timer = setTimeout(poll, delay);
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [active?.id, clipId, suggestionState]);

  const selectedModel = models.find((item) => item.model === model);
  const invalidRange = startFrame < 0 || endFrame < startFrame || endFrame >= frameCount;
  const completedRuns = runs.filter((run) => run.status === "succeeded");
  const latestTerminal = runs.find((run) => (
    run.status === "succeeded" || run.status === "failed" || run.status === "cancelled"
  )) ?? null;
  const latestFailed = latestTerminal?.status === "failed" ? latestTerminal : null;

  const start = async () => {
    if (busy || invalidRange || !selectedModel?.available) return;
    setBusy(true);
    setError(null);
    const key = `${clipId}:${clipRevision}:${model}:${startFrame}:${endFrame}`;
    const operationId = startAttempt.current?.key === key
      ? startAttempt.current.operationId : newOperationId();
    startAttempt.current = { key, operationId };
    try {
      const run = await startAssistanceRun(clipId, {
        operation_id: operationId, expected_clip_revision: clipRevision,
        model, start_frame: startFrame, end_frame: endFrame,
      });
      startAttempt.current = null;
      setRuns((current) => [run, ...current]);
      onQueueChanged();
    } catch { setError("Không thể đưa model vào hàng đợi."); }
    finally { setBusy(false); }
  };

  const cancel = async () => {
    if (!active || busy) return;
    setBusy(true);
    const operationId = cancelAttempts.current.get(active.id) ?? newOperationId();
    cancelAttempts.current.set(active.id, operationId);
    try {
      const next = await cancelAssistanceRun(
        clipId, active.id, { operation_id: operationId },
      );
      cancelAttempts.current.delete(active.id);
      setRuns((current) => current.map((item) => item.id === next.id ? next : item));
      onQueueChanged();
    } catch { setError("Không thể hủy lượt model."); }
    finally { setBusy(false); }
  };

  const reject = async (item: AssistanceSuggestionView) => {
    if (busy) return;
    setBusy(true);
    const operationId = rejectAttempts.current.get(item.id) ?? newOperationId();
    rejectAttempts.current.set(item.id, operationId);
    try {
      await rejectAssistanceSuggestion(clipId, item.id, {
        operation_id: operationId, expected_clip_revision: clipRevision,
      });
      rejectAttempts.current.delete(item.id);
      setSuggestions((current) => current.filter((candidate) => candidate.id !== item.id));
      onQueueChanged();
    } catch { setError("Không thể bỏ qua gợi ý này."); }
    finally { setBusy(false); }
  };

  return <section className="assisted-review" aria-labelledby="assisted-review-title">
    <div className="assisted-heading">
      <div><h3 id="assisted-review-title">Model hỗ trợ</h3><p>Model chỉ tìm đoạn cần xem; nhãn chỉ được lưu sau khi bạn kiểm tra.</p></div>
      {active && <span className="assist-status">{active.status === "queued" ? "Đang chờ" : `Đang chạy ${active.processed_frames}/${active.scheduled_frames || "…"}`}</span>}
    </div>
    <div className="assist-controls">
      <label>Model<select value={model} disabled={busy || Boolean(active)} onChange={(event) => setModel(event.target.value as typeof model)}>
        {models.map((item) => <option key={item.model} value={item.model} disabled={!item.available}>{MODEL_NAMES[item.model]}{item.available ? "" : " · chưa sẵn sàng"}</option>)}
      </select></label>
      <label>Frame bắt đầu hỗ trợ<input type="number" min={0} max={frameCount - 1} value={startFrame} disabled={busy || Boolean(active)} onChange={(event) => setStartFrame(Number(event.target.value))} /></label>
      <label>Frame kết thúc hỗ trợ<input type="number" min={0} max={frameCount - 1} value={endFrame} disabled={busy || Boolean(active)} onChange={(event) => setEndFrame(Number(event.target.value))} /></label>
      {active
        ? <button type="button" className="secondary" disabled={busy} onClick={() => void cancel()}>Hủy lượt model</button>
        : <button type="button" className="primary" disabled={busy || loading || invalidRange || !selectedModel?.available} onClick={() => void start()}>Chạy model</button>}
    </div>
    {invalidRange && <p className="error" role="alert">Khoảng frame hỗ trợ không hợp lệ.</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {!error && latestFailed && <p className="error" role="alert">Model thất bại: {latestFailed.error_code ?? "model_process_failed"}. Bạn có thể chọn đoạn ngắn hơn hoặc thử model khác.</p>}
    {!loading && models.length > 0 && !models.some((item) => item.available) && <p>Chưa có model cục bộ sẵn sàng; dán nhãn thủ công vẫn dùng bình thường.</p>}
    <div className="suggestion-queue">
      <div className="suggestion-heading"><strong>Đoạn model đề xuất</strong><span>{suggestions.length} mục</span></div>
      <label>Trạng thái gợi ý<select value={suggestionState} onChange={(event) => void changeSuggestionState(event.target.value as typeof suggestionState)}>
        <option value="pending">Đang chờ xem</option><option value="accepted">Đã dùng</option><option value="rejected">Đã bỏ qua</option><option value="stale">Đã lỗi thời</option>
      </select></label>
      {suggestions.map((item) => <article className="suggestion-card" key={item.id}>
        <button type="button" className="suggestion-range" onClick={() => onSeek(item.view_start_frame)}>Đoạn {item.view_start_frame}–{item.view_end_frame}</button>
        <div><strong>{item.label === "hand_in" ? "Hand in" : item.label === "hand_out" ? "Hand out" : "Chỉ cần xem"}</strong><span className="estimated-badge">ước lượng</span><small>{REASONS[item.reason]}{item.crossing_estimate != null ? ` · qua biên khoảng frame ${item.crossing_estimate}` : ""}</small></div>
        <div className="suggestion-actions">{item.review_state === "pending" ? <><button type="button" className="primary compact" disabled={busy} onClick={() => onUseSuggestion(item)}>Dùng làm nháp</button><button type="button" className="secondary compact" disabled={busy} onClick={() => void reject(item)}>Bỏ qua</button></> : <span>{item.review_state === "accepted" ? "Đã dùng" : item.review_state === "rejected" ? "Đã bỏ qua" : "Đã lỗi thời"}</span>}</div>
      </article>)}
      {!loading && suggestionState === "pending" && suggestions.length === 0 && completedRuns.length > 0 && <p className="assist-empty"><strong>Model không tìm thấy gợi ý đang chờ.</strong> Kết quả này không phải “không có hành động”; bạn vẫn phải xem phần còn lại hoặc ghi coverage ở bước Kiểm tra.</p>}
      {!loading && suggestionState === "pending" && suggestions.length === 0 && completedRuns.length === 0 && !active && !latestFailed && <p className="assist-empty">Chọn khoảng frame rồi chạy model để ưu tiên các đoạn quanh ROI.</p>}
      {!loading && suggestionState !== "pending" && suggestions.length === 0 && <p className="assist-empty">Chưa có gợi ý ở trạng thái này.</p>}
      {suggestionCursor && <button type="button" className="secondary compact" disabled={loadingMoreSuggestions} onClick={() => void loadMoreSuggestions()}>Tải thêm gợi ý</button>}
    </div>
    <details className="assist-history">
      <summary className="suggestion-heading"><strong>Lịch sử model</strong><span>{runs.length} lượt đã tải</span></summary>
      {runs.map((run) => <div key={run.id} className="assist-history-row">
        <span>{MODEL_NAMES[run.model]} · frame {run.start_frame}–{run.end_frame}</span>
        <strong>{run.status === "succeeded" ? "Hoàn tất" : run.status === "failed" ? `Thất bại · ${run.error_code ?? "model_process_failed"}` : run.status === "cancelled" ? "Đã hủy" : run.status === "running" ? `Đang chạy · ${run.processed_frames}/${run.scheduled_frames}` : "Đang chờ"}</strong>
      </div>)}
      {runCursor && <button type="button" className="secondary compact" disabled={loadingMoreRuns} onClick={() => void loadMoreRuns()}>Tải thêm lịch sử</button>}
    </details>
  </section>;
}
