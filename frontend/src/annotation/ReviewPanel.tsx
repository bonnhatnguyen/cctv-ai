import { useEffect, useMemo, useState } from "react";
import type { ReviewCoverageView } from "./types.generated";

const REVIEW_LABELS = [
  ["hand_in", "Hand in"],
  ["hand_out", "Hand out"],
  ["take_out", "Take out"],
  ["put_in", "Put in"],
] as const;

type ReviewLabel = typeof REVIEW_LABELS[number][0];
export type ReviewCoverageDraft = {
  start_frame: number;
  end_frame: number;
  reviewed_labels: ReviewLabel[];
};

export function ReviewPanel({ coverage, currentFrame, frameReady, busy, error, resetExactFrames = 0, onRecord, onDirtyChange }: {
  coverage: ReviewCoverageView[];
  currentFrame: number;
  frameReady: boolean;
  busy: boolean;
  error: string | null;
  resetExactFrames?: number;
  onRecord: (draft: ReviewCoverageDraft) => Promise<boolean>;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [startFrame, setStartFrame] = useState<number | null>(null);
  const [endFrame, setEndFrame] = useState<number | null>(null);
  const [labels, setLabels] = useState<ReviewLabel[]>([]);
  const [confirmedComplete, setConfirmedComplete] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const dirty = startFrame !== null || endFrame !== null || labels.length > 0 || confirmedComplete;
  const active = useMemo(() => coverage.filter((item) => item.active), [coverage]);
  const invalidated = useMemo(() => coverage.filter((item) => !item.active), [coverage]);

  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  useEffect(() => {
    if (!resetExactFrames) return;
    setStartFrame(null);
    setEndFrame(null);
    setConfirmedComplete(false);
  }, [resetExactFrames]);

  const toggle = (label: ReviewLabel) => {
    setConfirmedComplete(false);
    setLabels((current) => current.includes(label)
      ? current.filter((item) => item !== label)
      : REVIEW_LABELS.map(([value]) => value).filter((item) => current.includes(item) || item === label));
  };
  const submit = async () => {
    if (startFrame === null || endFrame === null) {
      setLocalError("Cần đặt frame bắt đầu và kết thúc của khoảng đã xem.");
      return;
    }
    if (endFrame < startFrame) {
      setLocalError("Frame kết thúc phải từ frame bắt đầu trở đi.");
      return;
    }
    if (!labels.length) {
      setLocalError("Chọn ít nhất một lớp hành động đã kiểm tra.");
      return;
    }
    if (!confirmedComplete) {
      setLocalError("Chỉ ghi nhận sau khi xác nhận đã xem toàn bộ khoảng.");
      return;
    }
    setLocalError(null);
    if (await onRecord({ start_frame: startFrame, end_frame: endFrame, reviewed_labels: labels })) {
      setStartFrame(null);
      setEndFrame(null);
      setLabels([]);
      setConfirmedComplete(false);
    }
  };

  return <section className="review-panel" aria-labelledby="review-coverage-title">
    <div className="review-panel-heading">
      <div><h3 id="review-coverage-title">Phạm vi đã kiểm tra</h3><p>Chưa được kiểm tra vẫn là chưa biết, không phải “không có hành động”.</p></div>
      <span>{active.length} khoảng đang hiệu lực</span>
    </div>
    <div className="review-capture-grid">
      <button type="button" disabled={!frameReady || busy} onClick={() => { setStartFrame(currentFrame); setConfirmedComplete(false); }}>Đặt bắt đầu <strong>{startFrame ?? "—"}</strong></button>
      <button type="button" disabled={!frameReady || busy} onClick={() => { setEndFrame(currentFrame); setConfirmedComplete(false); }}>Đặt kết thúc <strong>{endFrame ?? "—"}</strong></button>
    </div>
    <fieldset className="review-labels" disabled={busy}>
      <legend>Đã kiểm tra đầy đủ các lớp</legend>
      {REVIEW_LABELS.map(([value, text]) => <label key={value}><input type="checkbox" checked={labels.includes(value)} onChange={() => toggle(value)} />{text}</label>)}
    </fieldset>
    <label className="review-attestation"><input type="checkbox" disabled={busy} checked={confirmedComplete} onChange={(event) => setConfirmedComplete(event.target.checked)} />Tôi đã xem toàn bộ khoảng này và đã liệt kê hết event/unclear của các lớp được chọn.</label>
    {(localError || error) && <p className="error" role="alert">{localError || error}</p>}
    <button className="primary" type="button" disabled={busy || !confirmedComplete || startFrame === null || endFrame === null || labels.length === 0} onClick={() => void submit()}>{busy ? "Đang ghi…" : "Ghi nhận đã kiểm tra"}</button>

    {active.length > 0 && <div className="coverage-list"><h4>Coverage đang dùng</h4>{active.map((item) => <CoverageRow key={item.id} item={item} />)}</div>}
    {invalidated.length > 0 && <details className="coverage-history"><summary>Coverage cũ ({invalidated.length})</summary>{invalidated.map((item) => <CoverageRow key={item.id} item={item} invalidated />)}</details>}
  </section>;
}

function CoverageRow({ item, invalidated = false }: { item: ReviewCoverageView; invalidated?: boolean }) {
  return <div className={`coverage-row ${invalidated ? "invalidated" : ""}`}>
    <strong>Frame {item.start_frame}–{item.end_frame}</strong>
    <span>{item.reviewed_labels.join(", ")}</span>
    {invalidated && <em>Đã bị vô hiệu hóa</em>}
  </div>;
}
