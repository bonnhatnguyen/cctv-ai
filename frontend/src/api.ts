export type ReviewDecision = "confirmed" | "false_positive" | "insufficient_data";
export type ReviewCase = { id: string; status: string; reason: string; clips: Record<string, string | null>; event_timeline: { kind?: string; start_ms?: number }[] };

export async function listCases(): Promise<ReviewCase[]> {
  const response = await fetch("/api/review-cases?status=review_required");
  if (!response.ok) throw new Error("Không tải được hàng đợi xem lại");
  return response.json();
}
export async function decide(id: string, outcome: ReviewDecision, note?: string) {
  const response = await fetch(`/api/review-cases/${id}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ outcome, note }) });
  if (!response.ok) throw new Error("Không lưu được quyết định");
}
