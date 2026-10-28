import { describe, expect, it } from "vitest";
import {
  buildChildIndex,
  buildParentIndex,
  childProjectsAt,
  filterByQuery,
  findItemStatus,
  getDependencies,
  getDependents,
  getLearningSubtopicItems,
  indexProjectComponents,
  isListedProject,
  normalizeQuery,
  parseProjectNodeId,
  projectTree,
  ROOT_SLOT,
  withItemStatus,
} from "../derive";
import { makeLearning, makeProjects } from "./fixtures";

const projects = makeProjects();
const byId = new Map(projects.map((p) => [p.id, p]));
const platform = byId.get("platform")!;

describe("nested component index", () => {
  it("indexes components at every depth with phase and ancestors", () => {
    const index = indexProjectComponents(platform);
    expect([...index.keys()].sort()).toEqual(["a", "a1", "a2", "b", "c"]);
    const a2 = index.get("a2")!;
    expect(a2.phase.id).toBe("p1");
    expect(a2.ancestors.map((c) => c.id)).toEqual(["a", "a1"]);
    expect(index.get("c")!.phase.id).toBe("p2");
  });

  it("resolves dependencies and dependents, skipping unknown ids", () => {
    const index = indexProjectComponents(platform);
    expect(getDependencies(index, index.get("b")!.comp).map((e) => e.comp.id)).toEqual(["a"]);
    expect(getDependents(index, "b").map((e) => e.comp.id)).toEqual(["c"]);
  });

  it("sets and reads status at any depth without mutating", () => {
    const next = withItemStatus(platform, "a2", "done");
    expect(findItemStatus(next, "a2")).toBe("done");
    expect(findItemStatus(platform, "a2")).toBe("not_started");
    expect(findItemStatus(platform, "p1")).toBe("in_progress");
    expect(findItemStatus(platform, "zzz")).toBeUndefined();
  });
});

describe("child project index", () => {
  const parentOf = buildParentIndex(projects);
  const childIndex = buildChildIndex(projects, parentOf);

  it("only links children whose parent exists, with a breadcrumb path", () => {
    expect([...parentOf.keys()].sort()).toEqual(["api", "cli", "docs"]);
    const api = parentOf.get("api")!;
    expect(api.parentName).toBe("Platform");
    expect(api.phase).toEqual({ id: "p1", name: "Foundations" });
    expect(api.components.map((c) => c.id)).toEqual(["a", "a1"]);
    expect(parentOf.get("cli")!.phase?.id).toBe("p2");
    expect(parentOf.get("cli")!.components).toEqual([]);
  });

  it("slots children under their phase/component, unknown `under` at the root", () => {
    expect(childProjectsAt(childIndex, "platform", "a1").map((p) => p.id)).toEqual(["api"]);
    expect(childProjectsAt(childIndex, "platform", "p2").map((p) => p.id)).toEqual(["cli"]);
    expect(childProjectsAt(childIndex, "platform", ROOT_SLOT).map((p) => p.id)).toEqual(["docs"]);
    expect(childProjectsAt(childIndex, "orphan", ROOT_SLOT)).toEqual([]);
  });
});

describe("listing rule", () => {
  const listed = (searching: boolean) =>
    projects.filter((p) => isListedProject(p, byId, searching)).map((p) => p.id).sort();

  it("lists top-level projects and only active children when not searching", () => {
    expect(listed(false)).toEqual(["api", "orphan", "platform"]);
  });

  it("lists every match while searching", () => {
    expect(listed(true)).toEqual(["api", "cli", "docs", "orphan", "platform"]);
    const q = normalizeQuery("  GATEWAY ");
    const matches = filterByQuery(projects, q).filter((p) => isListedProject(p, byId, q !== ""));
    // name match (API Gateway) and description match (Docs), sorted by name
    expect(matches.map((p) => p.id)).toEqual(["api", "docs"]);
  });
});

describe("map tree", () => {
  const childIndex = buildChildIndex(projects, buildParentIndex(projects));
  const tree = projectTree(platform, childIndex.get("platform"));

  it("nests phases -> components -> nested components", () => {
    expect(tree.map((g) => g.id)).toEqual(["p1", "p2"]);
    const a = tree[0].children[0];
    expect(a.id).toBe("a");
    expect(a.children![0].id).toBe("a1");
    expect(a.children![0].children![0].id).toBe("a2");
  });

  it("puts child projects under their slot with namespaced ids", () => {
    const a1 = tree[0].children[0].children![0];
    const apiNode = a1.children!.find((c) => c.isProject)!;
    expect(apiNode.id).toBe("project:api");
    const routing = apiNode.children![0];
    expect(routing.id).toBe("project:api::g1");
    expect(routing.defaultOpen).toBe(true);
    expect(routing.foreign).toEqual({ projectId: "api", itemId: "g1" });
    expect(routing.children![0].id).toBe("project:api::r");
    // phase-level child sits after the phase's components
    expect(tree[1].children.map((c) => c.id)).toEqual(["c", "project:cli"]);
    expect(tree[1].children[1].children).toBeUndefined();
    expect(parseProjectNodeId("project:api::r")).toEqual({ projectId: "api", itemId: "r" });
    expect(parseProjectNodeId("a1")).toBeNull();
  });
});

describe("learning detail rows", () => {
  it("uses markdown bullets, else the summary", () => {
    const sub = makeLearning()[0].topics![0].subtopics![0];
    expect(getLearningSubtopicItems(sub, "- Nyquist\n* Folding\ntext").map((i) => i.text)).toEqual([
      "Nyquist",
      "Folding",
    ]);
    expect(getLearningSubtopicItems(sub, "").map((i) => i.kindLabel)).toEqual(["Core Concept"]);
  });
});

describe("text-field and subtree helpers", () => {
  it("applyTextFields removes optional keys on empty values and keeps name", async () => {
    const { applyTextFields, changedTextFields, subtreeIds } = await import("../derive");
    const item = { id: "x", name: "X", summary: "s", items: ["a"] };
    expect(applyTextFields(item, { summary: "", items: [], notes: "n" })).toEqual({ id: "x", name: "X", notes: "n" });
    expect(changedTextFields(item, { name: "X", summary: "s2", description: "" })).toEqual({ summary: "s2" });
    const platform = makeProjects()[0];
    expect([...subtreeIds(platform.phases![0].components![0])]).toEqual(["a", "a1", "a2"]);
    expect(subtreeIds(platform.phases![0]).has("b")).toBe(true);
  });
});
