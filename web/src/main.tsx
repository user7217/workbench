import React from "react";
import ReactDOM from "react-dom/client";
import { WorkbenchProvider } from "@workbench-core";
// The look: ./src/ui by default, or the folder named by WORKBENCH_UI (see vite.config.ts)
import View from "@workbench-ui";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <WorkbenchProvider>
      <View />
    </WorkbenchProvider>
  </React.StrictMode>
);
