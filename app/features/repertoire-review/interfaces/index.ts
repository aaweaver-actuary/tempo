/** Existing screen contracts; no proposal, session-state or content writer is constructed. */
export type {
  CommandOptions, ConflictRow, RepertoireNavigationIntent, RepertoireReviewClient,
  RepertoireReviewRead, RequestOptions, ReviewFinding, ReviewLine, ReviewPosition,
} from "../api";
export type { RepertoireReviewViewProps } from "../RepertoireReviewView";

export type RepertoireReviewComponent = typeof import("../RepertoireReviewView").RepertoireReviewView;

export async function loadRepertoireReviewView(): Promise<RepertoireReviewComponent> {
  return (await import("../RepertoireReviewView")).RepertoireReviewView;
}
