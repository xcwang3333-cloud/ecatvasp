import { mount } from "svelte";

import App from "./App.svelte";
import "./app.css";

const target = document.getElementById("app");
if (target === null) {
  throw new Error("desktop app root is missing");
}

mount(App, { target });
