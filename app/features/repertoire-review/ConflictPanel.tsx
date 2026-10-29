import type { BoardAppearance } from "@tempo/board-ui";
import { useState } from "react";
import { Button } from "../../components/buttons/BaseButton";
import { Surface } from "../../components/ui";
import type { ConflictRow, RepertoireReviewClient, RepertoireReviewRead } from "./api";
import { PositionViewer } from "./PositionViewer";
import { ProposalPanel } from "./ProposalPanel";
import type { RepertoireReviewSession } from "./state";

export function ConflictPanel({ conflict, read, api, session, appearance }: {
  appearance: BoardAppearance; conflict: ConflictRow; read: RepertoireReviewRead; api: RepertoireReviewClient; session: RepertoireReviewSession;
}) {
  const [choice, setChoice] = useState<string | null>(null);
  const selected = conflict.choices.find(item => item.move_uci === choice);
  return <Surface aria-label="Exact response conflict">
    <h3>Competing own responses</h3>
    <p>Exact position: <code>{conflict.position.position_key}</code></p>
    <p>Study block: {conflict.study_block.status} · {conflict.study_block.reason}</p>
    <p>Affected routes: {conflict.affected_routes.join(", ")}</p>
    <p>Choose a response, preview its precise content and card changes, then confirm.</p>
    <div className="repertoire-review-actions">{conflict.choices.map(item => <Button key={item.move_uci}
      aria-pressed={choice === item.move_uci} onClick={() => setChoice(item.move_uci)}>Choose {item.move_uci}</Button>)}</div>
    {selected
      ? <ProposalPanel appearance={appearance} key={`${selected.proposal.proposal_id}/${selected.proposal.proposal_revision}/${selected.proposal.display_digest}`}
          proposal={selected.proposal} read={read} api={api} session={session} position={conflict.position} />
      : <PositionViewer appearance={appearance} position={conflict.position} identity={conflict.conflict_id} />}
  </Surface>;
}
