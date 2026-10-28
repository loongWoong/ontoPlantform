import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/geist";
import "@fontsource-variable/newsreader";
import "@fontsource-variable/newsreader/wght-italic.css";
import "@fontsource-variable/geist-mono";
import "./styles.css";
import App from "./App";

const el = document.getElementById("root");
if (el) createRoot(el).render(<React.StrictMode><App /></React.StrictMode>);
