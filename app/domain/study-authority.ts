/** Retained work facts, independent of physical browser storage layout. */
export type RetainedWorkFact = {
  authority: string | null;
  disposition: "terminal" | "unresolved" | "unknown";
};
/** Byte-preserving inspection payload; authority policy must not interpret it. */
export type RetainedStudyExport = {
  databases: Record<string, Record<string, unknown[]>>;
  legacy: Record<string, string | null>;
};
export type RetainedAuthorityInspection = {
  facts: RetainedWorkFact[];
  retained: RetainedStudyExport;
};
