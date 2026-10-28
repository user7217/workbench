---
name: workbench
description: Record what this session built, finished, and left to do in the project's Workbench dashboard entry. Use when finishing meaningful work on a project, creating a new project, or responding to a Workbench Stop hook reminder.
---

# Workbench: record this session on the dashboard

The project's dashboard entry (`project.yaml`) is its long-term memory: each session adds what it
learned. You are the only one who knows what this session did, so your job is to turn this
conversation into an accurate update — what exists now, what is half-done, and what is left to do.
The "left to do" part is the point of the dashboard; never skip it.

## Sources, in order of trust

1. **This conversation** (including any compaction summary): what was built, fixed, decided,
   deferred, what is still failing, and what the user said comes next.
2. **The current entry** (`workbench show`): what earlier sessions recorded. Keep it unless this
   conversation shows it is wrong or out of date.
3. **Repo docs and code** (README, architecture/plan/progress/TODO files): only to fill an empty or
   newly discovered entry (`phases: []` and `topics: []`), or to check a structure you are unsure of.

## Which project?

- **Work inside one project's git repo** (the usual case, and every Stop hook reminder): run
  `workbench show` and `workbench begin` with no arguments from that repo; they find the project
  from the repo itself.
- **Work that is not in a git repo** (planning, research, hardware, chat-only sessions), **or work
  for a project that spans several repos**, or the user names the project: pass
  `--id <project-id>` to both `workbench show` and `workbench begin`. If unsure of the id, ask.
- **The project does not exist yet**: create it with `workbench new "<Project Name>"` (it prints
  the id), then use `--id`.
- **The user wants this repo to belong to that project**: run `workbench link --id <project-id>`
  from inside the repo. Only do this when the user asks.

## Steps

1. **Read the entry**: run `workbench show` (or `workbench show --id <project-id>`, see above).

2. **Take stock of this conversation** as three lists:
   - **Built / finished** — working now (tests pass, or the user saw it work) → `done`.
   - **In progress** — started but not complete or not yet working → `in_progress`.
   - **Left to do** — planned work → `not_started`. This counts:
     - what the user said should happen next or later ("next we need…", "after that…", "TODO");
     - work deferred, descoped for now, or split off during the session;
     - known failures that were not fixed (failing tests or checks, open bugs);
     - follow-ups you proposed that the user agreed to.

     This does **not** count: your own ideas the user did not agree to, or speculative features.

3. **Decide whether to update**: if the three lists add nothing to the entry (pure refactor,
   formatting, a small fix with no status change), run `workbench hook-check --mark-seen`, reply
   `"Workbench: no update needed"`, and stop. A newly agreed next step or a newly known failure
   **is** an update. (Outside a git repo there is nothing to mark seen; just reply and stop.)

4. **Edit**: run `workbench begin` (or `workbench begin --id <project-id>`) and edit the
   `project.yaml` path it prints. Merge — do not
   regenerate:
   - **Keep everything already there.** Update statuses, add new items, never rename an `id`.
     New ids are lowercase slugs (`^[a-z0-9]+(-[a-z0-9]+)*$`), unique across the whole file.
   - **Structure** — keep an existing `uses_phases` setting. For an empty entry, choose
     `uses_phases: true` with `phases:` if the work runs in stages, or `uses_phases: false` with
     `topics:` if it is a set of areas.
   - **Every phase or topic with real work — done, in progress, or planned — gets children**
     (`components:`, or `topics:` / `subtopics:`). A phase with only a summary shows as an empty box
     on the dashboard. Only a `parked` or not-yet-planned phase may have no children.
   - **Status** — child: `done` only when implemented and working; `in_progress` when partly built;
     `not_started` when planned; `parked` when the user put it on hold. A phase follows its
     children: `done` when all are done, `in_progress` when any has started.
   - **`depends_on`** — list component ids a component needs first (existing ids only, no cycles).
   - **Nesting** — a component can hold its own `components:` list, to any depth, for a part made of
     smaller parts. Ids stay unique across the whole project, nested ones included.
   - **Child projects** — a piece of work too big for one entry can be its own project. The
     *child* carries `parent: <project-id>` and `under: <phase-or-component-id>` (an item in the
     parent, at any depth); the parent's file is not changed. Create one with
     `workbench new "<Name>" --parent <project-id> --under <item-id>`, or place an existing project
     with `workbench attach --id <child> --parent <project-id> --under <item-id>`
     (`workbench detach --id <child>` undoes it). Only do this when the user agrees. A project has
     one parent at most. Record the child's detail in the child, not in the parent.
   - **Text fields** — `summary` (≤300 chars): what it is; for `not_started` work, what it is and
     why it is needed. `notes` (≤1000 chars): current state that will change — what is failing or
     blocking, counts, the next concrete step. `items` (each ≤300 chars): optional short checklist
     of concrete sub-tasks.
   - **Project fields** — keep `description` (≤500 chars) a one-paragraph explanation of the
     project. Project `status`: `planned` (not started yet), `active`, `paused`, `done`, `archived`;
     change it only if the user said so (`workbench new --status planned` for work not begun). A
     child project shows in the Projects tab only while `active`; otherwise it shows only inside
     its parent. Keep `parent`/`under` as they are. Do not edit `updated` or `origin`; the CLI sets
     them.
   - **Learning paths** may list `related_projects: [<project-id>, ...]` — a plain reference to
     projects the learning supports; no status flows either way.
   - **Privacy** — never include secrets, API keys, local paths (`/Users/...`, `~/...`), emails,
     private IPs, pasted logs or error output, or personal information.

   Example of recording the future:
   ```yaml
   - id: mapping-path
     name: Mapping path
     status: in_progress
     summary: Derivation sheets for the occupancy-grid mapping path.
     notes: 13 of 253 sheets fail checks; inverse_sensor_model is the only failure on this path.
     items:
       - Fix inverse_sensor_model sheet citations
   - id: second-project-nodes
     name: Second project nodes
     status: not_started
     summary: Atlas nodes for a second project, to test the pipeline beyond one codebase.
   ```

5. **Push**: run `workbench push <worktree> -m "<one-line summary>"`.
   - If validation or privacy checks fail, fix the file in the worktree and push again.
   - If push reports removed ids or a large deletion, you rewrote instead of merging: restore the
     missing items and push again. Pass `--allow-removals` only if the user explicitly asked to
     remove something.

6. **Report**: give the one-line result from `workbench push`, then list the `not_started` items you
   recorded so the user can correct them.
