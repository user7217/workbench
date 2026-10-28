import type { Project } from "../../types/project";
import type { LearningPath } from "../../types/learning";
import type { SnapshotData } from "../api";

/**
 * "platform" has phases p1 (components a > a1 > a2, b depends on a) and p2 (c).
 * Children: "api" (active, under a1), "cli" (planned, under p2), "docs" (paused, under an unknown
 * id -> root slot), "orphan" (parent missing -> top level).
 */
export function makeProjects(): Project[] {
  return [
    {
      id: "platform",
      name: "Platform",
      status: "active",
      updated: "2026-01-01",
      description: "Core platform services",
      phases: [
        {
          id: "p1",
          name: "Foundations",
          status: "in_progress",
          components: [
            {
              id: "a",
              name: "Auth",
              status: "in_progress",
              components: [
                { id: "a1", name: "Tokens", status: "done", components: [{ id: "a2", name: "Rotation" }] },
              ],
            },
            { id: "b", name: "Billing", depends_on: ["a", "missing"] },
          ],
        },
        { id: "p2", name: "Launch", components: [{ id: "c", name: "Console", depends_on: ["b"] }] },
      ],
    },
    { id: "api", name: "API Gateway", status: "active", updated: "2026-01-01", parent: "platform", under: "a1",
      phases: [{ id: "g1", name: "Routing", components: [{ id: "r", name: "Router" }] }] },
    { id: "cli", name: "CLI", status: "planned", updated: "2026-01-01", parent: "platform", under: "p2" },
    { id: "docs", name: "Docs", status: "paused", updated: "2026-01-01", parent: "platform", under: "nope",
      description: "Handbook for the gateway" },
    { id: "orphan", name: "Orphan", status: "done", updated: "2026-01-01", parent: "gone" },
  ];
}

export function makeLearning(): LearningPath[] {
  return [
    {
      id: "dsp",
      name: "Signal Processing",
      status: "active",
      updated: "2026-01-01",
      related_projects: ["platform", "ghost"],
      topics: [
        {
          id: "t1",
          name: "Sampling",
          status: "in_progress",
          subtopics: [{ id: "s1", name: "Aliasing", status: "not_started", summary: "Folding" }],
        },
      ],
    },
  ];
}

export function makeSnapshot(): SnapshotData {
  return {
    projects: makeProjects(),
    learning: makeLearning(),
    topic_markdowns: { "dsp/s1": "Intro\n- Nyquist rate\n* Folding\nplain" },
    commit: "abcdef1234567",
    synced_at: null,
  };
}
