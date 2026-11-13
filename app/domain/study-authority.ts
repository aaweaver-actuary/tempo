/** Semantic Study authority, independent of browser storage layout. */
export type AuthorityIdentity = string;
export type RegisteredAuthority = { identity: AuthorityIdentity; origin: string; workspace: string };

/** Semantic authority capabilities; locator contents remain storage-owned. */
export type StudyAuthorityStorage = {
  readRegisteredAuthorities(): RegisteredAuthority[];
  bindAuthority(authority: RegisteredAuthority): void;
  offlineStoreLocator(identity: AuthorityIdentity | null): string;
  readRetainedStudyData(validateStorage?: boolean): Promise<RetainedStudyExport>;
  inspectRetainedStudyAuthority(): Promise<RetainedAuthorityInspection>;
};

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
