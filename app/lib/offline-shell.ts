export const OFFLINE_SHELL_VERSION = "tempo-static-v5";
let confirmedShellVersion: string | undefined;

export function offlineShellVersion(): string | undefined {
  return confirmedShellVersion;
}

function withTimeout<T>(task: Promise<T>, timeoutMilliseconds: number, message: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => reject(new Error(message)), timeoutMilliseconds);
    task.then(
      (result) => { window.clearTimeout(timeout); resolve(result); },
      (error) => { window.clearTimeout(timeout); reject(error); },
    );
  });
}

export async function waitForOfflineShell(timeoutMilliseconds = 10_000): Promise<void> {
  if (!("serviceWorker" in navigator))
    throw new Error("Offline launch is unavailable in this browser. Open Tempo in iPhone Safari and add it to the Home Screen.");

  await withTimeout(navigator.serviceWorker.ready, timeoutMilliseconds,
    "Offline app shell is not ready. Reopen Tempo while connected.");
  const controller = navigator.serviceWorker.controller ?? await new Promise<ServiceWorker>((resolve, reject) => {
    const onControllerChange = () => {
      const activeController = navigator.serviceWorker.controller;
      if (!activeController) return;
      window.clearTimeout(timeout);
      navigator.serviceWorker.removeEventListener("controllerchange", onControllerChange);
      resolve(activeController);
    };
    const timeout = window.setTimeout(() => {
      navigator.serviceWorker.removeEventListener("controllerchange", onControllerChange);
      reject(new Error("Offline app shell is not controlling this page yet. Reopen Tempo while connected."));
    }, timeoutMilliseconds);
    navigator.serviceWorker.addEventListener("controllerchange", onControllerChange);
    onControllerChange();
  });

  await new Promise<void>((resolve, reject) => {
    const channel = new MessageChannel();
    const timeout = window.setTimeout(() => {
      channel.port1.close();
      reject(new Error("The phone has an older Tempo app shell. Reopen Tempo while connected to update it."));
    }, timeoutMilliseconds);
    channel.port1.onmessage = (event: MessageEvent<{ version?: string; ready?: boolean }>) => {
      window.clearTimeout(timeout);
      channel.port1.close();
      confirmedShellVersion = event.data?.version;
      if (event.data?.version !== OFFLINE_SHELL_VERSION)
        reject(new Error("The phone has an older Tempo app shell. Reopen Tempo while connected to update it."));
      else if (!event.data.ready)
        reject(new Error("The offline app shell is incomplete. Reopen Tempo while connected."));
      else resolve();
    };
    controller.postMessage({ type: "tempo:offline-shell-status" }, [channel.port2]);
  });
}
