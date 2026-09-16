import type { ComponentProps } from "react";
import type { RoiView } from "./types.generated";
import type { VideoMetadata } from "../trackingApi";
import { fittedImageRect } from "./geometry";

export function RoiVideo({ roi, media, ...videoProps }: ComponentProps<"video"> & {
  roi: RoiView | null;
  media: VideoMetadata;
}) {
  const [sarNum, sarDen] = media.sample_aspect_ratio.split(":").map(Number);
  const rect = fittedImageRect({ left: 0, top: 0, width: 16, height: 9 }, media.width, media.height, sarNum, sarDen);
  return <div className="tracking-video-stage">
    <div className="media-plane" style={{ width: `${rect.width / 16 * 100}%`, height: `${rect.height / 9 * 100}%` }}>
      <video {...videoProps} />
      {roi && <svg className="roi-overlay" viewBox="0 0 1 1" preserveAspectRatio="none" aria-label="ROI rổ tiền đã lưu">
        <polygon points={roi.polygon.map((p) => `${p.x},${p.y}`).join(" ")} fill="rgba(55,210,125,.16)" stroke="#56f09b" strokeWidth=".006" />
      </svg>}
    </div>
  </div>;
}
