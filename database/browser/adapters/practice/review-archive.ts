/** Existing durable browser persistence; application decisions stay with callers. */
const rejectedOpeningReviewStorageKey = "tempo-rejected-opening-reviews-v1";
export function readRejectedOpeningReviews() { return localStorage.getItem(rejectedOpeningReviewStorageKey); }
export function writeRejectedOpeningReviews(archived: readonly unknown[], rejected: unknown) { localStorage.setItem(rejectedOpeningReviewStorageKey, JSON.stringify([...archived, rejected])); }
