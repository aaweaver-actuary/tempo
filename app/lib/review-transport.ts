import type { StudyClient } from "./study-connection";

export class ReviewAuthorityChangedError extends Error {
  constructor() { super("Study authority changed. Recover the original review with its original service."); this.name = "ReviewAuthorityChangedError"; }
}

/** Capture one authority for a review, its marker, and its receipt checks. */
export function createReviewTransport(client: StudyClient) {
  const origin = client.origin, generation = client.generation;
  function requireCurrent() {
    if (client.origin !== origin || client.generation !== generation)
      throw new ReviewAuthorityChangedError();
  }
  return { client, origin, requireCurrent };
}
