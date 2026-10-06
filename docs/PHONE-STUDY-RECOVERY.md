# Recovering phone study

Saved phone reviews and opening journals remain on the device until the computer
confirms them. Keep that browser's data while recovering; clearing website data,
removing the home-screen app, or switching browsers can lose unsent work.

## Queue preparation

“Phone offline queue is refreshing” means the computer has not finished the new
offline queue. The existing saved queue is retained. Keep the computer connected
and retry loading the queue. Check Notifications → All for preparation status;
ordinary preparation does not show warning toasts. Repeated notices share one
history record. A confirmed prepared queue resolves earlier preparation warnings.

“Phone queue could not be prepared” indicates a real preparation or storage
failure. Follow its error message. If the computer reports a failed queue worker,
restore the local service before retrying. A successful live review does not by
itself prove that the complete offline queue is ready.

## Opening evidence

Pending checkpoint notices mean evidence remains saved locally. Normal training
continues. Recovery runs one checkpoint at a time after foreground work is ready,
with a bounded retry delay. Reopening while connected resumes recovery. An
uncertain delivery checks its original receipt and preserves its request identity
and captured events. Newer events wait for a later slice.

A blocked operation requires resolving its reported service error and explicitly
retrying that operation from operation status. A rejected or retained journal stays
available for diagnosis and does not claim evidence was saved. Other eligible
journals can continue recovering.

## Worker and timeout errors

If a study worker cannot start or its response cannot be decoded, reopen Tempo
while connected so its current worker bundle can load. Prepare the queue again
before depending on offline study. The offline-ready check includes the worker
and generated JavaScript dependencies.

A timed-out save has an uncertain outcome. Use Retry save or reconnect in the
same browser; recovery retains the original attempt identity. Discovery saves
also keep their selected choice. Existing clients repair a legacy oversized
discovery key only after confirmed rejection, preserving uncertain operations.

If an error persists, export Notifications and debug details for diagnosis. Include
the affected operation or attempt ID and whether the computer was connected.
This repair introduces no data migration and requires no browser-data reset.
