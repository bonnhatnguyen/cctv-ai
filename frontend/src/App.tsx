import { useState } from "react";
import { CaseDetail } from "./CaseDetail";
import { CaseList } from "./CaseList";
import { ReviewCase } from "./api";
export default function App() { const [selected, setSelected] = useState<ReviewCase>(); return <main>{selected ? <CaseDetail item={selected} onDone={() => setSelected(undefined)} /> : <CaseList onSelect={setSelected} />}</main>; }
