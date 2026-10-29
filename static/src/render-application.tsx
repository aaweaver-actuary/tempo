import "@tempo/board-ui/styles.css";
import React from "react";
import ReactDOM from "react-dom/client";
import "../../app/components/ui.css";
import "../../app/globals.css";
import "../../app/responsive.css";
import { Suspense, type ComponentType } from "react";
import { TempoErrorBoundary } from "../../app/components/error-boundary";
import { installGlobalDebugErrorHandlers, reportDebugError } from "../../app/lib/debug-reporting";
import { prepareMoveSounds } from "../../app/lib/move-sound";
import { NotificationViewport } from "../../app/components/notification-center";

export function renderApplication(Home: ComponentType) {
  installGlobalDebugErrorHandlers();
  prepareMoveSounds();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <TempoErrorBoundary><Suspense fallback={<p role="status">Loading Tempo…</p>}><Home /></Suspense></TempoErrorBoundary>
    <NotificationViewport />
  </React.StrictMode>,
);

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    let currentController = navigator.serviceWorker.controller;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      const previousController = currentController;
      currentController = navigator.serviceWorker.controller;
      if (!previousController) return;
      window.dispatchEvent(new Event("tempo:update-ready"));
    });
    void navigator.serviceWorker.register(new URL("sw.js", document.baseURI))
      .catch((error) => reportDebugError(error, {
        source: "service-worker", operation: "register offline shell",
      }));
  });
}

}
