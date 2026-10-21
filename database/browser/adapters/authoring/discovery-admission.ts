import type { PendingDiscoveryAdmission } from "../../../../app/domain/authoring-discovery-admission-record";

const storageKey = "tempo-pending-discovery-admissions-v1";

export function readDiscoveryAdmissionRecords(): unknown {
  const stored = localStorage.getItem(storageKey);
  return stored ? JSON.parse(stored) : undefined;
}

export function writeDiscoveryAdmissionRecords(admissions: PendingDiscoveryAdmission[]): void {
  localStorage.setItem(storageKey, JSON.stringify(admissions));
}
