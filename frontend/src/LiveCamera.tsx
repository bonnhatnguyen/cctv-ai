import Hls from "hls.js";
import { useEffect, useRef } from "react";

export type OverlayDetection = { track_id: string; kind: string; confidence: number; bbox: [number, number, number, number] };
export function LiveCamera({ cameraId, status, detections = [] }: { cameraId: string; status: string; detections?: OverlayDetection[] }) {
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (!video.current || status !== "live") return;
    const url = `/live/${cameraId}/index.m3u8`;
    const player = new Hls({ lowLatencyMode: true });
    player.loadSource(url); player.attachMedia(video.current); player.startLoad();
    return () => player.destroy();
  }, [cameraId, status]);
  return <figure><figcaption>{cameraId} <small>({status})</small></figcaption>{status === "live" ? <div className="video-wrap"><video ref={video} muted autoPlay controls />{detections.map((item) => <div className="detection" key={item.track_id} style={{ left: `${item.bbox[0] * 100}%`, top: `${item.bbox[1] * 100}%`, width: `${(item.bbox[2] - item.bbox[0]) * 100}%`, height: `${(item.bbox[3] - item.bbox[1]) * 100}%` }}><span>{item.kind} · {item.track_id} · {Math.round(item.confidence * 100)}%</span></div>)}</div> : <p>Đang chờ luồng camera: {status}</p>}</figure>;
}
