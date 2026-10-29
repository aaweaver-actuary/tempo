import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { RepertoireNavigationIntent } from "../repertoire-review/api";
import { RepertoireReviewSession } from "../repertoire-review/state";
import { createProductRepertoireReviewApi } from "./repertoire-review-api";

const Review = lazy(() => import("../repertoire-review/RepertoireReviewView").then(module => ({ default:module.RepertoireReviewView })));

/** Root supplies its retained authoring view and existing authenticated fetch. */
export function RepertoireAuditMount({ children, apiUrl, request, onNavigate }: {
  children?: ReactNode;
  apiUrl: string;
  request: typeof globalThis.fetch;
  onNavigate: (intent: RepertoireNavigationIntent) => void;
}) {
  const [tab,setTab] = useState<"authoring"|"audit">("authoring");
  const [repertoires,setRepertoires] = useState<readonly {id:string;name:string}[]>([]);
  const [selected,setSelected] = useState("");
  const selectedRef = useRef(selected);
  const [error,setError] = useState("");
  const [session] = useState(() => new RepertoireReviewSession());
  const [auditVersion,setAuditVersion] = useState(0);
  const [auditing,setAuditing] = useState(false);
  const auditOperations = useRef(new Map<string,string>());
  useEffect(() => {
    if (tab!=="audit") return;
    const controller = new AbortController();
    void request(`${apiUrl}/api/repertoires`, {signal:controller.signal}).then(async response => {
      if (!response.ok) throw new Error("Repertoires could not be loaded. Retry the audit tab.");
      const data = await response.json() as {repertoires?:{id:string;name:string}[]};
      if (!Array.isArray(data.repertoires) || data.repertoires.some(item =>
        typeof item.id!=="string" || typeof item.name!=="string")) throw new Error("Invalid repertoire list");
      if (!controller.signal.aborted) setRepertoires(data.repertoires.filter(item => !item.id.startsWith("__")));
    }).catch((failure:unknown) => {if (!controller.signal.aborted) setError(failure instanceof Error?failure.message:"Audit unavailable");});
    return () => controller.abort();
  },[tab,apiUrl,request]);
  const api = useMemo(() => createProductRepertoireReviewApi({ fetch:request, endpoints:{
    review:id=>`${apiUrl}/api/product/repertoires/${encodeURIComponent(id)}/review`,
    proposals:id=>`${apiUrl}/api/product/repertoires/${encodeURIComponent(id)}/proposals`,
    evidence:key=>`${apiUrl}/api/product/repertoires/${encodeURIComponent(selected)}/evidence?position_key=${encodeURIComponent(key)}`,
    preview:id=>`${apiUrl}/api/product/repertoires/${encodeURIComponent(id)}/changes/preview`,
    decide:`${apiUrl}/api/product/repertoires/${encodeURIComponent(selected)}/changes/decide`,
  }}),[apiUrl,request,selected]);
  async function enforceAudit() {
    if (!selected || auditing) return;
    const repertoire = selected;
    const operation = auditOperations.current.get(repertoire) ?? crypto.randomUUID();
    auditOperations.current.set(repertoire,operation);
    setAuditing(true);setError("");
    try {
      const response = await request(`${apiUrl}/api/product/repertoires/${encodeURIComponent(repertoire)}/audit`, {
        method:"POST",headers:{"Idempotency-Key":operation},
      });
      if (response.status===202) throw new Error("Conflict audit is still pending. Check the same audit again.");
      if (!response.ok) throw new Error("Conflict enforcement is unavailable. Keep the pending audit and retry.");
      auditOperations.current.delete(repertoire);
      setAuditVersion(value=>value+1);
    } catch(failure:unknown) {
      if (selectedRef.current===repertoire) setError(failure instanceof Error?failure.message:"Audit unavailable");
    }
    finally {setAuditing(false);}
  }
  return <section aria-label="Repertoire workspace">
    <nav aria-label="Repertoire tools">
      <button type="button" aria-pressed={tab==="authoring"} onClick={()=>setTab("authoring")}>Repertoire library</button>
      <button type="button" aria-pressed={tab==="audit"} onClick={()=>{setError("");setTab("audit");}}>Audit own responses</button>
    </nav>
    {tab==="authoring" ? children : <>
      <label>Audit repertoire <select value={selected} onChange={event=>{
        selectedRef.current=event.target.value;setSelected(event.target.value);
      }}>
        <option value="">Choose a repertoire</option>
        {repertoires.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
      <button type="button" disabled={!selected || auditing} onClick={()=>void enforceAudit()}>
        {auditing?"Checking conflict audit…":"Apply current conflict audit"}
      </button>
      {error && <p role="alert">{error}</p>}
      {selected && <Suspense fallback={<p>Loading repertoire audit…</p>}>
        <Review key={`${selected}:${auditVersion}`} api={api} repertoireId={selected} session={session} onNavigate={intent=>{
          if(intent.kind==="import"||intent.kind==="manual_edit"||intent.kind==="settings")setTab("authoring");
          onNavigate(intent);
        }}/>
      </Suspense>}
    </>}
  </section>;
}
