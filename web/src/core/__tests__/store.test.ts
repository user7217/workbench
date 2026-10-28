import { afterEach, describe, expect, it, vi } from "vitest";
import { createWorkbench, pendingKeys, selectView } from "../store";
import { findItemStatus } from "../derive";
import { makeSnapshot } from "./fixtures";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** A fetch mock whose response is released by the test, so the in-flight state can be inspected. */
function deferredFetch() {
  let release!: (r: Response) => void;
  const fetchMock = vi.fn(() => new Promise<Response>((res) => (release = res)));
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, release: (r: Response) => release(r) };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("optimistic status writes", () => {
  it("applies an item status immediately, then reverts it when the POST fails", async () => {
    const { fetchMock, release } = deferredFetch();
    const wb = createWorkbench({ initialState: { snapshot: makeSnapshot() } });
    const platform = () => wb.getState().snapshot.projects.find((p) => p.id === "platform")!;
    const key = pendingKeys.projectItem("platform", "a2");

    wb.actions.setItemStatus("platform", "a2", "done");

    expect(findItemStatus(platform(), "a2")).toBe("done");
    expect(wb.getState().pending[key]).toBe(true);
    expect(selectView(wb.getState()).isPending(key)).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/projects/platform/items/a2/status");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ "X-Workbench": "1", "Content-Type": "application/json" });
    expect(JSON.parse(init.body as string)).toEqual({ status: "done" });

    // A second write to the same target is ignored while the first is in flight
    wb.actions.setItemStatus("platform", "a2", "parked");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    release(jsonResponse(409, { detail: "Validation failed" }));
    await flush();

    expect(findItemStatus(platform(), "a2")).toBe("not_started");
    expect(wb.getState().pending[key]).toBeUndefined();
    expect(wb.getState().lastError?.message).toBe(
      "Could not save status update — reverted change. Validation failed"
    );
    wb.actions.dismissError();
    expect(wb.getState().lastError).toBeNull();
  });

  it("adopts the server's snapshot and status on success", async () => {
    const { release } = deferredFetch();
    const wb = createWorkbench({ initialState: { snapshot: makeSnapshot() } });
    const serverSnap = { ...makeSnapshot(), commit: "fff0000" };
    const serverStatus = { last_good_commit: "fff0000", last_checked_at: null, last_error: null };

    wb.actions.setProjectStatus("platform", "paused");
    expect(wb.getState().snapshot.projects[0].status).toBe("paused");

    release(jsonResponse(200, { ok: true, snapshot: serverSnap, status: serverStatus }));
    await flush();

    expect(wb.getState().snapshot.commit).toBe("fff0000");
    expect(wb.getState().syncStatus.last_good_commit).toBe("fff0000");
    expect(wb.getState().lastError).toBeNull();
  });

  it("does not POST when the status is unchanged", () => {
    const { fetchMock } = deferredFetch();
    const wb = createWorkbench({ initialState: { snapshot: makeSnapshot() } });
    wb.actions.setProjectStatus("platform", "active");
    wb.actions.setItemStatus("platform", "p1", "in_progress");
    wb.actions.setLearningStatus("dsp", "active");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reverts a learning item status when the network fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("offline"))));
    const wb = createWorkbench({ initialState: { snapshot: makeSnapshot() } });
    const sub = () => wb.getState().snapshot.learning[0].topics![0].subtopics![0];

    wb.actions.setLearningItemStatus("dsp", "s1", "done");
    expect(sub().status).toBe("done");
    await flush();

    expect(sub().status).toBe("not_started");
    expect(wb.getState().lastError?.message).toContain("reverted change. offline");
  });
});

describe("refresh and selection", () => {
  it("loads snapshot and status on refresh", async () => {
    const snap = makeSnapshot();
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve(url === "/api/snapshot" ? jsonResponse(200, snap) : jsonResponse(200, { last_error: null }))
      )
    );
    const wb = createWorkbench();
    await wb.actions.refresh();
    expect(wb.getState().snapshot.projects).toHaveLength(snap.projects.length);
  });

  it("selecting a child project reveals its spot in the parent", () => {
    const wb = createWorkbench({ initialState: { snapshot: makeSnapshot() } });
    wb.actions.selectProject("api");
    const s = wb.getState();
    expect(s.route).toEqual({ page: "project", id: "api" });
    expect(s.selection.phaseId).toBe("g1");
    expect(s.expanded.projects).toMatchObject({ api: true, platform: true });
    expect(s.expanded.phases.p1).toBe(true);
    expect(s.expanded.nestedComponents).toMatchObject({ a: true, a1: true });
    expect(selectView(s).activeParent?.parentId).toBe("platform");
  });

  it("opening a nested component selects its phase and expands its ancestors", () => {
    const wb = createWorkbench({ initialState: { snapshot: makeSnapshot() } });
    wb.actions.openProjectAt("platform", "a2");
    const s = wb.getState();
    expect(s.selection).toMatchObject({ phaseId: "p1", componentId: "a2" });
    expect(s.expanded.nestedComponents).toMatchObject({ a: true, a1: true });
    expect(selectView(s).activeComponent?.comp.id).toBe("a2");
  });

  it("selecting a subtopic selects and expands its topic", () => {
    const wb = createWorkbench({ initialState: { snapshot: makeSnapshot() } });
    wb.actions.selectLearningPath("dsp", null, "s1");
    const v = selectView(wb.getState());
    expect(wb.getState().selection).toMatchObject({ topicId: "t1", subtopicId: "s1" });
    expect(v.activeLearningItem?.id).toBe("s1");
    expect(v.activeMarkdown).toContain("Nyquist");
  });
});

describe("text edits (optimistic)", () => {
  it("applies a component summary edit at once, then reverts it when the POST fails", async () => {
    const { fetchMock, release } = deferredFetch();
    const wb = createWorkbench({ initialState: { snapshot: makeSnapshot() } });
    const comp = () => selectView(wb.getState()).componentIndex(wb.getState().snapshot.projects[0]).get("a1")!.comp;
    const key = pendingKeys.itemFields("platform", "a1");

    const done = wb.actions.updateItemFields("platform", "a1", { name: "Tokens", summary: "JWT + refresh" });

    expect(comp().summary).toBe("JWT + refresh");
    expect(wb.getState().pending[key]).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/projects/platform/items/a1/fields");
    // Only the changed field is sent
    expect(JSON.parse(init.body as string)).toEqual({ summary: "JWT + refresh" });

    release(jsonResponse(422, { detail: "Validation failed" }));
    expect(await done).toBe(false);

    expect(comp().summary).toBeUndefined();
    expect(wb.getState().pending[key]).toBeUndefined();
    expect(wb.getState().lastError).toMatchObject({
      message: "Could not save edit — reverted change. Validation failed",
      detail: "Validation failed",
    });
  });

  it("clearing a field removes the key, and the revert restores it", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("offline"))));
    const wb = createWorkbench({ initialState: { snapshot: makeSnapshot() } });
    const project = () => wb.getState().snapshot.projects[0];

    const done = wb.actions.updateProjectFields("platform", { description: "" });
    expect("description" in project()).toBe(false);
    expect(await done).toBe(false);
    expect(project().description).toBe("Core platform services");
  });

  it("does not POST unchanged fields or an empty name", async () => {
    const { fetchMock } = deferredFetch();
    const wb = createWorkbench({ initialState: { snapshot: makeSnapshot() } });
    expect(await wb.actions.updateLearningFields("dsp", { name: "Signal Processing", description: "" })).toBe(true);
    expect(await wb.actions.updateLearningItemFields("dsp", "s1", { name: "  " })).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(wb.getState().lastError?.message).toBe("Name can't be empty.");
  });

  it("edits a learning subtopic and adopts the server snapshot", async () => {
    const serverSnap = { ...makeSnapshot(), commit: "beef000" };
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, { ok: true, snapshot: serverSnap }))));
    const wb = createWorkbench({ initialState: { snapshot: makeSnapshot() } });
    expect(await wb.actions.updateLearningItemFields("dsp", "s1", { notes: "Read ch. 3" })).toBe(true);
    expect(wb.getState().snapshot.commit).toBe("beef000");
  });
});

describe("structure edits (server-authoritative)", () => {
  it("addItem posts, marks the structure key, adopts the snapshot and resolves to the new id", async () => {
    const { fetchMock, release } = deferredFetch();
    const wb = createWorkbench({ initialState: { snapshot: makeSnapshot() } });
    const key = pendingKeys.structure("platform");

    const added = wb.actions.addItem("platform", { parentId: "p2", name: "Metrics", summary: "Dashboards" });
    // Not optimistic: nothing changes until the server answers
    expect(selectView(wb.getState()).componentIndex(wb.getState().snapshot.projects[0]).has("metrics")).toBe(false);
    expect(wb.getState().pending[key]).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/projects/platform/items");
    expect(JSON.parse(init.body as string)).toEqual({ parent_id: "p2", name: "Metrics", summary: "Dashboards" });

    const serverSnap = makeSnapshot();
    serverSnap.projects[0].phases![1].components!.push({ id: "metrics", name: "Metrics", status: "not_started" });
    release(jsonResponse(200, { ok: true, snapshot: serverSnap, item_id: "metrics" }));

    expect(await added).toBe("metrics");
    expect(wb.getState().pending[key]).toBeUndefined();
    expect(selectView(wb.getState()).componentIndex(wb.getState().snapshot.projects[0]).has("metrics")).toBe(true);
  });

  it("a refused delete leaves the snapshot alone and surfaces the server's detail", async () => {
    const detail = "Cannot delete 'a': still referenced by depends_on (b -> a)";
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(422, { detail }))));
    const before = makeSnapshot();
    const wb = createWorkbench({ initialState: { snapshot: before } });

    expect(await wb.actions.deleteItem("platform", "a")).toBe(false);
    expect(wb.getState().snapshot).toBe(before);
    expect(wb.getState().lastError).toMatchObject({ message: `Could not delete item. ${detail}`, detail });
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/projects/platform/items/a/delete");
    expect(JSON.parse(init.body as string)).toEqual({ confirm: true });
  });

  it("a successful delete clears a selection that pointed into the deleted subtree", async () => {
    const serverSnap = makeSnapshot();
    serverSnap.projects[0].phases![0].components = [{ id: "b", name: "Billing" }];
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, { ok: true, snapshot: serverSnap }))));
    const wb = createWorkbench({ initialState: { snapshot: makeSnapshot() } });
    wb.actions.openProjectAt("platform", "a2");

    expect(await wb.actions.deleteItem("platform", "a")).toBe(true);
    expect(wb.getState().selection).toMatchObject({ phaseId: "p1", componentId: null });
  });

  it("moveItem and setParent send the server's body shapes", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(200, { ok: true })));
    vi.stubGlobal("fetch", fetchMock);
    const wb = createWorkbench({ initialState: { snapshot: makeSnapshot() } });

    expect(await wb.actions.moveItem("platform", "b", { position: 0 })).toBe(true);
    expect(await wb.actions.moveItem("platform", "b", { parentId: "p2", position: 1 })).toBe(true);
    expect(await wb.actions.setParent("cli", { parent: "platform", under: "c" })).toBe(true);
    expect(await wb.actions.setParent("cli", null)).toBe(true);

    const calls = (fetchMock.mock.calls as unknown as [string, RequestInit][]).map(([u, i]) => [
      u,
      JSON.parse(i.body as string),
    ]);
    expect(calls).toEqual([
      ["/api/projects/platform/items/b/move", { position: 0 }],
      ["/api/projects/platform/items/b/move", { parent_id: "p2", position: 1 }],
      ["/api/projects/cli/parent", { parent: "platform", under: "c" }],
      ["/api/projects/cli/parent", { parent: null, under: null }],
    ]);
  });
});
