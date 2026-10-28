/**
 * React binding of the store: <WorkbenchProvider> owns one store, `useWorkbench()` returns state,
 * derived view and actions as ONE object (see `Workbench`). This is the only React file in core/.
 */
import { createContext, createElement, useContext, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import {
  createWorkbench,
  selectView,
  type CreateWorkbenchOptions,
  type WorkbenchActions,
  type WorkbenchState,
  type WorkbenchStore,
  type WorkbenchView,
} from "./store";

/** Everything a look needs: state fields, derived values/helpers, and actions (all stable names). */
export type Workbench = WorkbenchState & WorkbenchView & WorkbenchActions;

const StoreContext = createContext<WorkbenchStore | null>(null);

export interface WorkbenchProviderProps {
  /** Use an existing store (tests, embedding); otherwise one is created from `options`. */
  store?: WorkbenchStore;
  options?: CreateWorkbenchOptions;
  children?: ReactNode;
}

/** Creates (or adopts) the store, loads data on mount and stops polling on unmount. */
export function WorkbenchProvider({ store, options, children }: WorkbenchProviderProps) {
  const [owned] = useState(() => store ?? createWorkbench(options));
  useEffect(() => {
    owned.start();
    return () => owned.stop();
  }, [owned]);
  return createElement(StoreContext.Provider, { value: owned }, children);
}

/** The raw store (for non-render code such as event handlers outside React). */
export function useWorkbenchStore(): WorkbenchStore {
  const store = useContext(StoreContext);
  if (!store) throw new Error("useWorkbench() must be used inside <WorkbenchProvider>");
  return store;
}

/** State + derived view + actions as one object; re-renders on every store change. */
export function useWorkbench(): Workbench {
  const store = useWorkbenchStore();
  const state = useSyncExternalStore(store.subscribe, store.getState, store.getState);
  return useMemo(() => ({ ...state, ...selectView(state), ...store.actions }), [state, store]);
}
