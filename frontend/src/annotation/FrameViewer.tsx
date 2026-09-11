import { useEffect, useRef, useState } from "react";
import { fittedImageRect, normalizePoint } from "./geometry";
import type { ClipView, Point } from "./types.generated";
import type { FrameCandidate, FrameToken } from "./useExactFrame";

function sar(value: string): [number, number] {
  const [left, right] = value.split(":").map(Number);
  return [left || 1, right || 1];
}

export function FrameViewer({ clip, index, onIndex, candidate, onFrameLoaded, onFrameError, playing, onPlaybackChange, points, onPoint, onMovePoint }: {
  clip: ClipView;
  index: number;
  onIndex: (index: number) => void;
  candidate: FrameCandidate | null;
  onFrameLoaded: (token: FrameToken) => void;
  onFrameError: (token: FrameToken) => void;
  playing: boolean;
  onPlaybackChange: (playing: boolean) => void;
  points: Point[];
  onPoint: (point: Point) => void;
  onMovePoint?: (index: number, point: Point) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const plane = useRef<HTMLDivElement>(null);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [playbackError, setPlaybackError] = useState<string | null>(null);
  const dragging = useRef<number | null>(null);
  const dragged = useRef(false);
  const media = clip.media!;
  const [sarNum, sarDen] = sar(media.sample_aspect_ratio);
  const rect = fittedImageRect({ left: 0, top: 0, width: 16, height: 9 }, media.width, media.height, sarNum, sarDen);
  const planeSize = { width: `${rect.width / 16 * 100}%`, height: `${rect.height / 9 * 100}%` };
  const stopAtFrame = (frame: number) => {
    video.current?.pause();
    onPlaybackChange(false);
    onIndex(Math.max(0, Math.min(media.frame_count - 1, frame)));
  };
  const setPlaying = () => {
    setPlaybackError(null);
    if (playing) {
      const frame = Math.round((video.current?.currentTime ?? 0) * media.fps_num / media.fps_den);
      stopAtFrame(frame);
    } else {
      onPlaybackChange(true);
    }
  };
  const chooseFrame = (value: number) => {
    if (Number.isInteger(value)) stopAtFrame(value);
  };
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest(".clip-tracking")) return;
      if (target && (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable)) return;
      if (event.key === " " || event.key === "ArrowLeft" || event.key === "ArrowRight") event.preventDefault();
      if (event.key === " ") void setPlaying();
      if (!playing && event.key === "ArrowLeft") onIndex(Math.max(0, index - 1));
      if (!playing && event.key === "ArrowRight") onIndex(Math.min(media.frame_count - 1, index + 1));
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });
  const click = (event: React.MouseEvent<HTMLDivElement>) => {
    if (playing) return;
    if (dragged.current) {
      dragged.current = false;
      return;
    }
    const image = event.currentTarget.getBoundingClientRect();
    const point = normalizePoint(event.clientX, event.clientY, image);
    if (point) onPoint(point);
  };
  return <div className="frame-viewer">
    <div className="roi-stage" data-testid="roi-stage">
      <div ref={plane} className="media-plane" data-testid="media-plane" onClick={click}
        onPointerMove={(event) => {
          if (playing || dragging.current === null || !onMovePoint) return;
          const point = normalizePoint(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect());
          if (point) {
            dragged.current = true;
            onMovePoint(dragging.current, point);
          }
        }}
        onPointerUp={() => { dragging.current = null; }}
        onPointerCancel={() => { dragging.current = null; }}
        style={{ ...planeSize, aspectRatio: `${media.width * sarNum} / ${media.height * sarDen}` }}>
      {playing && clip.preview_url && <video ref={video} muted src={clip.preview_url} style={{ objectFit: "fill" }}
        onLoadedMetadata={(event) => {
          const element = event.currentTarget;
          element.currentTime = index * media.fps_den / media.fps_num;
          element.playbackRate = playbackRate;
          void element.play().catch(() => {
            if (video.current !== element) return;
            setPlaybackError("Không thể phát preview. Hãy thử lại.");
            onPlaybackChange(false);
          });
        }}
        onEnded={() => stopAtFrame(media.frame_count - 1)}
        onError={() => { setPlaybackError("Không thể đọc preview của clip này."); onPlaybackChange(false); }} />}
      {!playing && candidate && <img style={{ objectFit: "fill" }}
        data-testid="exact-frame"
        data-clip-id={candidate.token.clipId}
        data-index={candidate.token.index}
        data-generation={candidate.token.generation}
        data-source-hash={candidate.token.sourceHash}
        src={candidate.url}
        alt={`Khung hình chính xác ${candidate.token.index}`}
        onLoad={() => onFrameLoaded(candidate.token)}
        onError={() => onFrameError(candidate.token)}
      />}
      <svg className="roi-overlay" viewBox="0 0 1 1" preserveAspectRatio="none" aria-label="ROI rổ tiền">
        {playing && clip.roi && <polygon points={clip.roi.polygon.map((p) => `${p.x},${p.y}`).join(" ")} fill="rgba(55,210,125,.16)" stroke="#56f09b" strokeWidth=".006" />}
        {!playing && points.length > 1 && <polyline points={points.map((p) => `${p.x},${p.y}`).join(" ")} fill="rgba(55,210,125,.16)" stroke="#56f09b" strokeWidth=".006" />}
        {!playing && points.map((point, position) => <circle key={position} data-testid="roi-point" className="roi-point" cx={point.x} cy={point.y} r=".012" fill="#fff" stroke="#1c9c5b" strokeWidth=".005"
          onPointerDown={(event) => { event.stopPropagation(); dragging.current = position; dragged.current = false; }} />)}
      </svg>
      </div>
    </div>
    {playbackError && <p className="error" role="alert">{playbackError}</p>}
    {playing && <p className="preview-note">{clip.roi ? "Đang hiển thị ROI đã lưu của clip này." : "Clip này chưa có ROI đã lưu."}</p>}
    <div className="frame-controls">
      <button type="button" onClick={() => void setPlaying()}>{playing ? "Tạm dừng" : "Phát preview"}</button>
      <label>Tốc độ preview <select value={playbackRate} onChange={(event) => {
        const rate = Number(event.target.value);
        setPlaybackRate(rate);
        if (video.current) video.current.playbackRate = rate;
      }}><option value="0.25">0,25×</option><option value="0.5">0,5×</option><option value="1">1×</option></select></label>
      <input type="range" min={0} max={media.frame_count - 1} value={index} onChange={(e) => chooseFrame(Number(e.target.value))} aria-label="Chọn khung hình" />
      <label>Frame <input data-testid="annotation-frame-input" type="number" min={0} max={media.frame_count - 1} value={index} onChange={(e) => chooseFrame(Number(e.target.value))} /></label>
    </div>
  </div>;
}
