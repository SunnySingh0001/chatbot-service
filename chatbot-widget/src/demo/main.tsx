/**
 * React 18 DOM mount point for the demo application.
 */
import React from "react";
import ReactDOM from "react-dom/client";
import { DemoApp } from "./DemoApp";

const rootElement = document.getElementById("root");
if (rootElement) {
  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      <DemoApp />
    </React.StrictMode>
  );
}
