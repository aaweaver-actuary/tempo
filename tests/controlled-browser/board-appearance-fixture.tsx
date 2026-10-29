// Actual application consumers; only their semantic data/transport inputs are synthetic.
import { useLayoutEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { resolveBoardAppearance } from "@tempo/board-ui/appearance";
import "@tempo/board-ui/styles.css";
import "../../app/components/ui.css";
import "../../app/globals.css";
import "../../app/responsive.css";
import { PersistentBoardShell } from "../../app/components/board/persistent-board-shell";
import TrainingView from "../../app/views/training_view";
import DefenseTrainingView from "../../app/views/defense_training_view";
import EndgamesView from "../../app/views/endgames_view";
import StudyExerciseRunner from "../../app/views/study_exercise_runner";
import TacticsView from "../../app/views/tactics_view";
import StudiesView from "../../app/views/studies_view";
import CardEditor from "../../app/views/card_editor";
import { TreeBrowser } from "../../app/views/tree_browser";
import { TacticCaptureDialog } from "../../app/components/tactic-capture-dialog";
import { CanonicalPrefixDialog } from "../../app/components/canonical-prefix-dialog";
import { OpeningSegmentation } from "../../app/components/opening-segmentation";
import { RepertoireIntegrityDialog } from "../../app/components/repertoire-integrity-dialog";
import { GameReviewView } from "../../app/features/game-review/GameReviewView";
import { RepertoireReviewView } from "../../app/features/repertoire-review/RepertoireReviewView";
import type { GameReviewClient } from "../../app/features/game-review/api";
import type { RepertoireReviewClient, RepertoireReviewRead } from "../../app/features/repertoire-review/api";
import { GameDetailSchema, ProposalSchema } from "../../app/features/product-api/contracts";
import { useTrainingStore } from "../../app/state/training-store";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { initializeStudyIntentStorage } from "../../database/browser/study-intent-storage";
import { STANDARD_FEN } from "../../app/const";
import { asCardId, asFenString, asSanMove, asQueueEntryId } from "../../app/types";
import detailFixture from "../../contracts/product-v02/fixtures/game-detail.json";
import proposalFixture from "../../contracts/product-v02/fixtures/proposal.json";

const card = { id: asCardId("appearance-card"), title: "Appearance opening", subtitle: "Synthetic", kind: "opening" as const,
  startingFen: asFenString(STANDARD_FEN), moves: [asSanMove("e4")], userMoveTarget: 1, orientation: "white" as const };
const defenseCard = { ...card, id: asCardId("appearance-defense"), backendId: asCardId("appearance-defense"), defenseCandidateId: "appearance-candidate", queueEntryId: asQueueEntryId(41), kind: "defense" as const,
  startingFen: asFenString("4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1") };
const detail = GameDetailSchema.parse(detailFixture.payload);
const gameApi: GameReviewClient = {
  recentGames: async () => ({ games: [detail], sync: [] }), gameDetail: async () => detail,
  sync: async () => { throw new Error("Appearance fixture must not sync games"); },
  retryFeedback: async () => { throw new Error("Appearance fixture must not write feedback"); },
};
const proposal = ProposalSchema.parse(proposalFixture.payload);
const position = { variant: "standard" as const, starting_fen: STANDARD_FEN, target_fen: STANDARD_FEN,
  history_uci: [], learner_color: "white" as const, position_key: proposal.edits[0].position_key };
const read: RepertoireReviewRead = {
  repertoire_id: proposal.repertoire_id, source_revision: proposal.source_revision, source_digest: proposal.source_digest,
  lines: [{ route_id: "appearance-line", label: "Appearance line", position }],
  conflicts: [{ conflict_id: "appearance-conflict", repertoire_id: proposal.repertoire_id, position, affected_routes: ["appearance-line"],
    study_block: { status: "blocked", reason: "Synthetic competing responses" }, choices: [{ move_uci: "a2a3", proposal }] }],
  findings: [{ finding_id: "appearance-finding", kind: "missing_reply", label: "Appearance missing reply", position, proposal_id: proposal.proposal_id,
    reach_probability: 0.2, reply_uci: "a2a3", missing_mass: null, evidence_status: "current", probability_evidence: [], similarity_differences: [], existing_card: null }],
};
const reviewApi: RepertoireReviewClient = { review: async () => read, proposals: async () => [proposal], evidence: async () => [],
  preview: async () => { throw new Error("Appearance fixture must not submit proposals"); },
  decide: async () => { throw new Error("Appearance fixture must not decide proposals"); },
};
const surfaces = ["persistent", "opening", "defense", "exercise", "tactics", "endgames", "studies", "card-editor", "capture", "tree", "canonical", "segmentation", "integrity", "games", "repertoire"];
const noop = () => {};
const done = async () => {};
function Fixture() {
  const [surface, setSurface] = useState("persistent");
  const [appearance, setAppearance] = useState(() => resolveBoardAppearance({ theme: localStorage.getItem("tempo-board-theme"), pieceSet: localStorage.getItem("tempo-piece-set") }));
  const { theme, pieceSet } = appearance;
  const shared = { theme, pieceSet };
  const training = { boardTheme: theme, pieceSet };
  useLayoutEffect(() => { useBoardShellStore.setState(state => ({ board: { ...state.board, theme, pieceSet } })); }, [theme, pieceSet]);
  const [moves, setMoves] = useState(0);
  return <>
    <button id="fixture-change-look" hidden onClick={() => setAppearance(value => value.theme === "blue" ? { theme: "green", pieceSet: "cburnett" } : { theme: "blue", pieceSet: "merida" })}>Change fixture look</button>
    <output id="fixture-move-count">{moves}</output>
    <label>Fixture consumer<select id="appearance-consumer" value={surface} onChange={event => setSurface(event.target.value)}>{surfaces.map(name => <option key={name}>{name}</option>)}</select></label>
    <div key={surface} id="appearance-consumer-root" style={{ maxWidth: 740, margin: "12px auto", padding: 12 }}>
      {surface === "persistent" && <PersistentBoardShell />}
      {surface === "opening" && <TrainingView {...training} dateLabel="Synthetic" serviceError="" refreshDatabaseQueue={noop} cardsLeft={1} card={card}
        rateCard={done} handleAttemptFailure={noop} resetCardAttempt={noop} onMove={() => setMoves(value => value + 1)} />}
      {surface === "defense" && <DefenseTrainingView {...training} card={defenseCard} useSharedBoard={false} onAdvance={done} />}
      {surface === "exercise" && <StudyExerciseRunner {...training} studyId="appearance-study" exerciseId="appearance-exercise" />}
      {surface === "tactics" && <TacticsView {...shared} onQueueChanged={noop} />}
      {surface === "endgames" && <EndgamesView {...shared} scheduledCard={{ ...card, kind: "endgame", moves: [] }} onQueueChanged={noop} />}
      {surface === "studies" && <><PersistentBoardShell /><StudiesView {...training} /></>}
      {surface === "card-editor" && <CardEditor {...training} practiceCard={card} onClose={noop} onSave={noop} />}
      {surface === "capture" && <TacticCaptureDialog {...shared} onClose={noop} onQueueChanged={noop} />}
      {surface === "tree" && <TreeBrowser {...shared} onClose={noop} />}
      {surface === "canonical" && <CanonicalPrefixDialog {...shared} repertoireId="appearance-repertoire" repertoireName="Appearance prefix" side="white" onClose={noop} onSaved={done} />}
      {surface === "segmentation" && <OpeningSegmentation {...shared} repertoireId="appearance-repertoire" initiallyOpened />}
      {surface === "integrity" && <RepertoireIntegrityDialog {...shared} repertoireId="appearance-repertoire" onClose={noop} />}
      {surface === "games" && <GameReviewView appearance={appearance} api={gameApi} />}
      {surface === "repertoire" && <RepertoireReviewView appearance={appearance} api={reviewApi} repertoireId={proposal.repertoire_id} />}
    </div>
  </>;
}
useTrainingStore.setState({ currentFenString: asFenString(STANDARD_FEN), step: 0, feedback: "ready", showHint: false, attempt: { entryKey: "appearance-card", generation: 1, phase: "playerTurn" } });
await initializeStudyIntentStorage();
createRoot(document.getElementById("root")!).render(<Fixture />);
