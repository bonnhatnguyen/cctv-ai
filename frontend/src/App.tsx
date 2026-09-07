import { useEffect, useState } from "react";
import { CaseDetail } from "./CaseDetail";
import { CaseList } from "./CaseList";
import { ReviewCase } from "./api";
import { LiveCamera } from "./LiveCamera";
export default function App() {
  const [selected, setSelected] = useState<ReviewCase>(); const [live, setLive] = useState<Record<string, string>>({ "cam-a": "checking", "cam-b": "checking" });
  useEffect(() => { const update = () => fetch("/api/live/status").then(r => r.json()).then(setLive).catch(() => setLive({ "cam-a": "api_unavailable", "cam-b": "api_unavailable" })); update(); const id = window.setInterval(update, 3000); return () => clearInterval(id); }, []);
  return <main><header><h1>Giám sát giao dịch</h1><p>Quan sát video thuần túy; không kết luận hành vi của bất kỳ ai.</p></header><section><h2>Camera trực tiếp</h2><div><LiveCamera cameraId="cam-a" status={live["cam-a"]} /><LiveCamera cameraId="cam-b" status={live["cam-b"]} /></div></section>{selected ? <CaseDetail item={selected} onDone={() => setSelected(undefined)} /> : <CaseList onSelect={setSelected} />}</main>;
}
