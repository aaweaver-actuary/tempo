"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "./buttons/BaseButton";
import {
  DISCOVERY_ADMISSIONS_CHANGED,
  DISCOVERY_ADMISSION_QUEUED,
  flushPendingDiscoveryAdmissions,
  pendingDiscoveryAdmissions,
  recoverUnacknowledgedDiscoveryAdmissions,
  reportDiscoveryAdmissionErrors,
  retryPendingDiscoveryAdmission,
  type PendingDiscoveryAdmission,
} from "../lib/discovery-admission-outbox";
import { notifications, resolveNotification } from "../lib/notifications";

function errorMessage(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

export function PendingDiscoverySaves({
  onQueueChanged,
  interactionBlocked = false,
}: {
  onQueueChanged: () => Promise<void>;
  interactionBlocked?: boolean;
}) {
  const [admissions, setAdmissions] = useState<PendingDiscoveryAdmission[]>([]);
  const [readError, setReadError] = useState<string | null>(null);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<Set<string>>(() => new Set());
  const recoveryStarted = useRef(false);
  const onQueueChangedRef = useRef(onQueueChanged);

  useEffect(() => {
    onQueueChangedRef.current = onQueueChanged;
  }, [onQueueChanged]);

  useEffect(() => {
    const updatePending = () => {
      let current: PendingDiscoveryAdmission[];
      try {
        current = pendingDiscoveryAdmissions();
      } catch (cause) {
        setReadError(errorMessage(cause, "Could not read pending discovery saves"));
        return;
      }
      setAdmissions(current);
      setReadError(null);
      try {
        reportDiscoveryAdmissionErrors(current);
      } catch (cause) {
        setOperationError(errorMessage(cause, "Could not report discovery save errors"));
      }
    };

    if (!recoveryStarted.current) {
      recoveryStarted.current = true;
      try {
        recoverUnacknowledgedDiscoveryAdmissions();
      } catch {
        // updatePending below exposes malformed or unavailable storage.
      }
    }

    const onQueued = (event: Event) => {
      try {
        const { opportunityId } = (event as CustomEvent<{ opportunityId: string }>).detail;
        const notificationKey = `discovery-save:${opportunityId}`;
        const priorSaveNotice = notifications().find((record) =>
          record.key === notificationKey && !record.resolvedAt);
        if (priorSaveNotice) {
          resolveNotification(priorSaveNotice.id, {
            severity: "success",
            message: "Discovery save confirmed.",
          });
        }
      } catch (cause) {
        setOperationError(errorMessage(cause, "Could not resolve the discovery save notification"));
      }

      void Promise.resolve()
        .then(() => onQueueChangedRef.current())
        .catch((cause) => setOperationError(errorMessage(cause, "Could not refresh the training queue")));
    };

    const flush = async () => {
      try {
        await flushPendingDiscoveryAdmissions();
      } catch (cause) {
        setOperationError(errorMessage(cause, "Could not check discovery saves"));
      }
    };

    updatePending();
    window.addEventListener(DISCOVERY_ADMISSIONS_CHANGED, updatePending);
    window.addEventListener(DISCOVERY_ADMISSION_QUEUED, onQueued);
    window.addEventListener("storage", updatePending);
    void flush();
    const interval = window.setInterval(() => void flush(), 3_000);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener(DISCOVERY_ADMISSIONS_CHANGED, updatePending);
      window.removeEventListener(DISCOVERY_ADMISSION_QUEUED, onQueued);
      window.removeEventListener("storage", updatePending);
    };
  }, []);

  const retrySave = async (opportunityId: string) => {
    setRetrying((current) => new Set(current).add(opportunityId));
    try {
      await retryPendingDiscoveryAdmission(opportunityId);
    } catch (cause) {
      setOperationError(errorMessage(cause, "Could not retry the discovery save"));
    } finally {
      setRetrying((current) => {
        const next = new Set(current);
        next.delete(opportunityId);
        return next;
      });
    }
  };

  if (admissions.length === 0 && !readError && !operationError) return null;

  return (
    <details className="pending-discovery-saves">
      <summary className="pending-discovery-trigger" aria-label="Saved discovery requests">
        {admissions.length > 0 ? `Saves (${admissions.length})` : "Saves"}
      </summary>
      <section className="pending-discovery-panel" aria-label="Saved discovery requests">
        <h2>Saved discovery requests</h2>
        {readError && <p role="alert">{readError}</p>}
        {operationError && <p role="alert">{operationError}</p>}
        {admissions.length > 0 && (
          <ul className="pending-discovery-records">
            {admissions.map((admission) => (
              <li className="pending-discovery-record" key={admission.opportunityId}>
                <p>
                  <strong>{admission.state}</strong>: Discovery <span className="pending-discovery-identifier">{admission.opportunityId}</span> · move {admission.selectedMoveUci}
                </p>
                {admission.error && <p role={admission.state === "failed" ? "alert" : "status"}>{admission.error}</p>}
                {admission.state === "failed" && (
                  <Button
                    type="button"
                    disabled={interactionBlocked || retrying.has(admission.opportunityId)}
                    pending={retrying.has(admission.opportunityId)}
                    onClick={() => void retrySave(admission.opportunityId)}
                  >
                    Retry save
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </details>
  );
}
