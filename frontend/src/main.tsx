import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";

import { App } from "./app/App";

import "./styles/foundation.css";
import "./styles/workspace.css";


const root = document.getElementById("root");

if (root === null) {
  throw new Error("Frontend root element was not found.");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);