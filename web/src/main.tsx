import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import { ObservatoryPage } from "./features/observability/ObservatoryPage";
import "./styles.css";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("Missing #root element");
}

const isObservatory =
  new URLSearchParams(globalThis.location.search).get("view") ===
  "observatory";

createRoot(root).render(
  <React.StrictMode>
    {isObservatory ? <ObservatoryPage /> : <App />}
  </React.StrictMode>,
);
