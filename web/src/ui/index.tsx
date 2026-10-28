/**
 * Look contract: this file default-exports the view component. main.tsx mounts it inside
 * <WorkbenchProvider>, so the view reads everything through useWorkbench() from "@workbench-core".
 * A replacement look is any folder with an index.tsx like this one (select it with WORKBENCH_UI).
 */
import { App } from "./App";
import { EditSessionProvider } from "./components/Editing";
// After App so these rules override library CSS (e.g. @xyflow/react) as before
import "./styles.css";

/** The default look: the app plus its "one inline editor open at a time" session. */
export default function DefaultLook() {
  return (
    <EditSessionProvider>
      <App />
    </EditSessionProvider>
  );
}
