import { useEffect, useState } from "react";
import { listCases, ReviewCase } from "./api";

const reasonText: Record<string, string> = { cash_removal_has_no_observed_destination: "Tiền rời rổ chưa có điểm đến quan sát được" };
export function CaseList({ onSelect }: { onSelect: (item: ReviewCase) => void }) {
  const [cases, setCases] = useState<ReviewCase[]>([]);
  const [error, setError] = useState<string>();
  useEffect(() => { listCases().then(setCases).catch(() => setError("Không tải được hàng đợi xem lại")); }, []);
  if (error) return <p role="alert">{error}</p>;
  return <section><h1>Cần xem lại</h1>{cases.length === 0 && <p>Chưa có case nào cần xem lại.</p>}{cases.map((item) => <button key={item.id} onClick={() => onSelect(item)}><strong>{reasonText[item.reason] ?? item.reason}</strong><br /><small>Mã case: {item.id}</small></button>)}</section>;
}
