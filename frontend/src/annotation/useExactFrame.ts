import { useCallback, useEffect, useRef, useState } from "react";

export type FrameToken = {
  clipId: string;
  sourceHash: string;
  index: number;
  generation: number;
};

export type FrameCandidate = { token: FrameToken; url: string };
export type ExactFrameState = {
  candidate: FrameCandidate | null;
  displayed: FrameToken | null;
  loading: boolean;
  error: string | null;
  confirmLoaded: (token: FrameToken) => void;
  rejectLoad: (token: FrameToken) => void;
};

function sameToken(left: FrameToken | null, right: FrameToken) {
  return left !== null && left.clipId === right.clipId && left.sourceHash === right.sourceHash &&
    left.index === right.index && left.generation === right.generation;
}

export function useExactFrame(
  clipId: string,
  sourceHash: string | null,
  index: number,
): ExactFrameState {
  const generation = useRef(0);
  const current = useRef<FrameToken | null>(null);
  const objectUrl = useRef<string | null>(null);
  const [candidate, setCandidate] = useState<FrameCandidate | null>(null);
  const [displayed, setDisplayed] = useState<FrameToken | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const token = sourceHash === null ? null : {
      clipId, sourceHash, index, generation: ++generation.current,
    };
    current.current = token;
    setDisplayed(null);
    setCandidate(null);
    setError(null);
    if (objectUrl.current) {
      URL.revokeObjectURL(objectUrl.current);
      objectUrl.current = null;
    }
    if (!token) {
      setLoading(false);
      return () => controller.abort();
    }
    setLoading(true);
    fetch(`/api/v2/annotations/clips/${encodeURIComponent(clipId)}/frames/${index}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Không thể tải khung hình (${response.status}).`);
        if (
          response.headers.get("X-Frame-Index") !== String(index) ||
          response.headers.get("X-Source-SHA256") !== sourceHash
        ) throw new Error("Khung hình phản hồi không khớp lựa chọn hiện tại.");
        return response.blob();
      })
      .then((blob) => {
        if (controller.signal.aborted || !sameToken(current.current, token)) return;
        const url = URL.createObjectURL(blob);
        objectUrl.current = url;
        setCandidate({ token, url });
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted || !sameToken(current.current, token)) return;
        setError(reason instanceof Error ? reason.message : "Không thể tải khung hình.");
      })
      .finally(() => {
        if (sameToken(current.current, token)) setLoading(false);
      });
    return () => controller.abort();
  }, [clipId, sourceHash, index]);

  useEffect(() => () => {
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
  }, []);

  const confirmLoaded = useCallback((token: FrameToken) => {
    if (sameToken(current.current, token)) setDisplayed(token);
  }, []);
  const rejectLoad = useCallback((token: FrameToken) => {
    if (!sameToken(current.current, token)) return;
    setDisplayed(null);
    setError("Không thể hiển thị khung hình đã tải.");
  }, []);

  return { candidate, displayed, loading, error, confirmLoaded, rejectLoad };
}
