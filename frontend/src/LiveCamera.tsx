import Hls from "hls.js";
import { useEffect, useRef } from "react";

export function LiveCamera({ cameraId, status }: { cameraId: string; status: string }) {
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (!video.current || status !== "live") return;
    const url = `/live/${cameraId}/index.m3u8`;
    const player = new Hls({ lowLatencyMode: true });
    player.loadSource(url); player.attachMedia(video.current); player.startLoad();
    return () => player.destroy();
  }, [cameraId, status]);
  return <figure><figcaption>{cameraId} <small>({status})</small></figcaption>{status === "live" ? <video ref={video} muted autoPlay controls /> : <p>Đang chờ luồng camera: {status}</p>}</figure>;
}
