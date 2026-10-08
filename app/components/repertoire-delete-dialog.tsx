import { useState } from "react";
import { Dialog } from "./dialog";
import { Button } from "./buttons/BaseButton";
import type { LearnedCardsPolicy } from "../lib/repertoire-delete-command";

export function RepertoireDeleteDialog({ name, pendingPolicy, busy, error, localDemo = false, onClose, onConfirm }: {
  name: string; pendingPolicy?: LearnedCardsPolicy; busy: boolean; error: string;
  localDemo?: boolean;
  onClose: () => void; onConfirm: (policy: LearnedCardsPolicy) => Promise<void>;
}) {
  const [policy, setPolicy] = useState<LearnedCardsPolicy>(pendingPolicy ?? (localDemo ? "delete" : "keep"));
  return <Dialog className="deletion-dialog" titleId="delete-repertoire-title" onClose={() => { if (!busy) onClose(); }}>
    <h2 id="delete-repertoire-title">Delete “{name}”?</h2>
    <p>{localDemo ? "This removes this practice repertoire and its browser-stored cards." : "This removes its source lines and unlearned cards. Shared cards remain in their other repertoires."}</p>
    {!localDemo && <fieldset disabled={busy || pendingPolicy !== undefined}>
      <legend>Cards you have already learned</legend>
      <label><input type="radio" name="learned-cards" checked={policy === "keep"} onChange={() => setPolicy("keep")} /> Keep learned cards</label>
      <p>Move them to Retained cards and continue reviewing with the same schedule and history.</p>
      <label><input type="radio" name="learned-cards" checked={policy === "delete"} onChange={() => setPolicy("delete")} /> Delete learned cards</label>
      <p>Permanently remove exclusive learned cards and their training history.</p>
    </fieldset>}
    {pendingPolicy && <p role="status">Deletion is awaiting confirmation. Retry to check the same choice.</p>}
    {error && <p role="alert">{error}</p>}
    <div className="editor-actions">
      <Button disabled={busy} onClick={onClose}>{pendingPolicy ? "Close" : "Cancel"}</Button>
      <Button variant="danger" pending={busy} onClick={() => void onConfirm(policy)}>{pendingPolicy ? "Check deletion" : "Delete repertoire"}</Button>
    </div>
  </Dialog>;
}
