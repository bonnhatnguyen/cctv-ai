import { useState } from "react";
import { decide, ReviewCase, ReviewDecision } from "./api";
const labels: Record<ReviewDecision, string> = { confirmed: "Xác nhận", false_positive: "Báo sai", insufficient_data: "Không đủ dữ liệu" };
export function CaseDetail({ item, onDone }: { item: ReviewCase; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  async function submit(outcome: ReviewDecision) { setBusy(true); try { await decide(item.id, outcome); onDone(); } finally { setBusy(false); } }
  return <section><h2>Clip bằng chứng</h2><div>{Object.entries(item.clips).map(([camera, url]) => url ? <figure key={camera}><figcaption>{camera}</figcaption><video controls src={url} /></figure> : <p key={camera}>{camera}: camera_degraded</p>)}</div><h3>Dòng thời gian</h3><ol>{item.event_timeline.map((event, index) => <li key={index}>{event.kind ?? "sự kiện"} — {event.start_ms ?? ""}</li>)}</ol>{(Object.keys(labels) as ReviewDecision[]).map((outcome) => <button disabled={busy} key={outcome} onClick={() => submit(outcome)}>{labels[outcome]}</button>)}</section>;
}
