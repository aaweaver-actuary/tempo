import React from "react";
import ReactDOM from "react-dom/client";
import "@lichess-org/chessground/assets/chessground.base.css";
import "@lichess-org/chessground/assets/chessground.brown.css";
import "@lichess-org/chessground/assets/chessground.cburnett.css";
import "../../app/components/ui.css";
import "../../app/globals.css";
import "../../app/responsive.css";
import Home from "../../app/views/home_view";
import { TempoErrorBoundary } from "../../app/components/error-boundary";
import { installGlobalDebugErrorHandlers, reportDebugError } from "../../app/lib/debug-reporting";
import { prepareMoveSounds } from "../../app/lib/move-sound";
import { NotificationViewport } from "../../app/components/notification-center";

installGlobalDebugErrorHandlers();
prepareMoveSounds();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <TempoErrorBoundary><Home /></TempoErrorBoundary>
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
