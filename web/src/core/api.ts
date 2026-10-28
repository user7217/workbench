/**
 * Every HTTP call the dashboard makes. Nothing else in the app calls fetch().
 *
 * Writes are POSTs with `X-Workbench: 1` + JSON body; the server answers `{ok, snapshot, status}`
 * on success and `{detail}` on failure.
 */
import type { Project } from "../types/project";
import type { LearningPath } from "../types/learning";

export interface SnapshotData {
  projects: Project[];
  learning: LearningPath[];
  /** Markdown notes keyed by `${pathId}/${topicOrSubtopicId}`. */
  topic_markdowns: Record<string, string>;
  /** When each project / learning path last changed in git (ISO 8601), keyed by id. Older servers omit it. */
  updated_at?: { projects?: Record<string, string>; learning?: Record<string, string> };
  commit: string | null;
  synced_at: string | null;
}

export interface SyncStatus {
  last_good_commit: string | null;
  last_checked_at: string | null;
  last_error: {
    commit: string;
    file: string;
    message: string;
  } | null;
}

/** Server answer to a successful write. Either part may be missing. */
export interface WriteResult {
  ok?: boolean;
  snapshot?: SnapshotData;
  status?: SyncStatus;
  /** Id of the item created by an add (POST /api/projects/{id}/items). */
  item_id?: string;
}

export const EMPTY_SNAPSHOT: SnapshotData = {
  projects: [],
  learning: [],
  topic_markdowns: {},
  commit: null,
  synced_at: null,
};

export const EMPTY_SYNC_STATUS: SyncStatus = {
  last_good_commit: null,
  last_checked_at: null,
  last_error: null,
};

const WRITE_HEADERS = { "X-Workbench": "1", "Content-Type": "application/json" };

const enc = encodeURIComponent;

/** POST JSON to a write endpoint; resolves with the server's result, rejects with its `detail`. */
export async function postJson(url: string, body: unknown): Promise<WriteResult> {
  const resp = await fetch(url, {
    method: "POST",
    headers: WRITE_HEADERS,
    body: JSON.stringify(body),
  });
  let data: (WriteResult & { detail?: unknown }) | null = null;
  try {
    data = await resp.json();
  } catch {
    // Non-JSON body (e.g. proxy error page)
  }
  if (!resp.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : `Server responded ${resp.status}`);
  }
  return data ?? {};
}

/** Snapshot and sync status, fetched together. A part that fails to load is returned as null. */
export async function fetchAll(): Promise<{ snapshot: SnapshotData | null; status: SyncStatus | null }> {
  try {
    const [snapRes, statRes] = await Promise.all([fetch("/api/snapshot"), fetch("/api/status")]);
    return {
      snapshot: snapRes.ok ? await snapRes.json() : null,
      status: statRes.ok ? await statRes.json() : null,
    };
  } catch {
    // Offline or transient network error
    return { snapshot: null, status: null };
  }
}

/** Ask the server to pull the data repo now. Resolves true when the server accepted it. */
export async function triggerSync(): Promise<boolean> {
  try {
    const res = await fetch("/api/sync", { method: "POST", headers: WRITE_HEADERS, body: JSON.stringify({}) });
    return res.ok;
  } catch {
    return false;
  }
}

/** Status write endpoints, one per level. */
export const statusUrls = {
  project: (projectId: string) => `/api/projects/${enc(projectId)}/status`,
  projectItem: (projectId: string, itemId: string) =>
    `/api/projects/${enc(projectId)}/items/${enc(itemId)}/status`,
  learning: (pathId: string) => `/api/learning/${enc(pathId)}/status`,
  learningItem: (pathId: string, itemId: string) =>
    `/api/learning/${enc(pathId)}/items/${enc(itemId)}/status`,
};

/** Text-field write endpoints (same levels as statusUrls). */
export const fieldUrls = {
  project: (projectId: string) => `/api/projects/${enc(projectId)}/fields`,
  projectItem: (projectId: string, itemId: string) =>
    `/api/projects/${enc(projectId)}/items/${enc(itemId)}/fields`,
  learning: (pathId: string) => `/api/learning/${enc(pathId)}/fields`,
  learningItem: (pathId: string, itemId: string) =>
    `/api/learning/${enc(pathId)}/items/${enc(itemId)}/fields`,
};

/** Project structure endpoints (add / move / delete items, parent link). Projects only. */
export const structureUrls = {
  add: (projectId: string) => `/api/projects/${enc(projectId)}/items`,
  move: (projectId: string, itemId: string) => `/api/projects/${enc(projectId)}/items/${enc(itemId)}/move`,
  remove: (projectId: string, itemId: string) =>
    `/api/projects/${enc(projectId)}/items/${enc(itemId)}/delete`,
  parent: (projectId: string) => `/api/projects/${enc(projectId)}/parent`,
};

export function postStatus(url: string, status: string): Promise<WriteResult> {
  return postJson(url, { status });
}

/** The API surface the store uses; tests and alternative backends can pass their own. */
export interface WorkbenchApi {
  fetchAll: typeof fetchAll;
  triggerSync: typeof triggerSync;
  postJson: typeof postJson;
}

export const defaultApi: WorkbenchApi = { fetchAll, triggerSync, postJson };
