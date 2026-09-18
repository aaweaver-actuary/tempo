import React from "react";
import ReactDOM from "react-dom/client";
import "@lichess-org/chessground/assets/chessground.base.css";
import "@lichess-org/chessground/assets/chessground.brown.css";
import "@lichess-org/chessground/assets/chessground.cburnett.css";
import "../../app/globals.css";
import "../../app/responsive.css";
import Home from "../../app/views/home_view";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><Home /></React.StrictMode>,
);

if ("serviceWorker" in navigator && import.meta.env.PROD && import.meta.env.BASE_URL === "/tempo/") {
  window.addEventListener("load", () => {
    let reloading = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (reloading || crossOriginIsolated) return;
      reloading = true;
      location.reload();
    });
    void navigator.serviceWorker.register(new URL("sw.js", document.baseURI));
  });
}
