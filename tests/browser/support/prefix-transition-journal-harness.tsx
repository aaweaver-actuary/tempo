import React from "react";
import { createRoot, type Root } from "react-dom/client";
import { PrefixTransitionPanel } from "../../../app/components/prefix-transition-panel";
import RepertoireView from "../../../app/views/repertoire_view";
import { prefixSourceSchema } from "../../../app/domain/prefix-comparison";
import { prefixTransitionPlanSchema, prefixTransitionApplySchema, transitionSummary } from "../../../app/domain/prefix-transition";
import structural from "../../fixtures/prefix-comparison/structural.json";
import transition from "../../fixtures/prefix-comparison/transition.json";
export * from "../../../app/lib/prefix-transition-command";
export const request = prefixTransitionApplySchema.parse(transition.request);
export const summary = transitionSummary(prefixTransitionPlanSchema.parse(transition.ready), prefixSourceSchema.parse(structural.source), "Combined Black");
let root: Root | undefined;
export function unmount() { root?.unmount(); root = undefined; }
export function mountPanel() {
  unmount(); root = createRoot(document.getElementById("audit-root")!);
  root.render(<PrefixTransitionPanel repertoireId="rep" repertoireName="Combined Black" source={null}
    selectedIds={[]} depth={null} onBack={() => undefined} onPublished={async () => undefined} />);
}
export function mountRepertoire() {
  unmount(); root = createRoot(document.getElementById("audit-root")!);
  root.render(<RepertoireView imported={[]} onImport={() => undefined} onBrowse={() => undefined}
    onResolveGap={() => undefined} onShowGamesAtPosition={() => undefined} onRepair={() => undefined}
    onDeleteLocal={() => undefined} onRenameLocal={() => undefined} onQueueChanged={async () => undefined}
    onTrain={() => undefined} />);
}
