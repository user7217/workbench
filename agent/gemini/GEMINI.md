<!-- WORKBENCH:START -->
## Workbench Project Tracking

The project's Workbench entry (`project.yaml`) is its long-term memory on the dashboard. When finishing
meaningful work on a project, creating a new project, or prompted by the Workbench hook, turn this
conversation into an update: what exists now, what is half-done, and what is left to do. The "left to
do" part is the point of the dashboard; never skip it.

Sources, in order of trust: (1) this conversation — what was built, fixed, decided, deferred, still
failing, and what the user said comes next; (2) the current entry from `workbench show` — keep it unless
this conversation shows it is wrong; (3) repo docs and code — only to fill an empty entry
(`phases: []` and `topics: []`) or check a structure.

Which project: inside one project's git repo, run `workbench show` / `workbench begin` with no
arguments. For work not in a git repo (planning, research, chat-only), for a project spanning several
repos, or when the user names the project, pass `--id <project-id>` to both (ask if unsure of the id).
If the project does not exist yet, create it with `workbench new "<Project Name>"`. If the user wants
the current repo to belong to that project, run `workbench link --id <project-id>` inside the repo.

1. Run `workbench show` (or `workbench show --id <project-id>`).
2. Take stock of this conversation as three lists:
   - Built / finished (working now) → `done`.
   - In progress (started, not complete or not working yet) → `in_progress`.
   - Left to do → `not_started`: what the user said comes next or later, work deferred or split off,
     known unfixed failures (failing tests or checks, open bugs), and follow-ups you proposed that the
     user agreed to. Not your own unagreed ideas or speculative features.
3. If the lists add nothing to the entry (pure refactor, formatting, a small fix with no status change),
   run `workbench hook-check --mark-seen`, reply "Workbench: no update needed", and stop. A newly agreed
   next step or newly known failure is an update. (Outside a git repo, just reply and stop.)
4. Run `workbench begin` (or `workbench begin --id <project-id>`) and edit the printed `project.yaml`.
   Merge — do not regenerate:
   - Keep everything already there; update statuses, add items, never rename an `id`. New ids are
     unique lowercase slugs.
   - Keep an existing `uses_phases` setting. For an empty entry use `uses_phases: true` with `phases:` if
     the work runs in stages, or `uses_phases: false` with `topics:` if it is a set of areas.
   - Every phase or topic with real work — done, in progress, or planned — gets children (`components:`,
     or `topics:` / `subtopics:`). Only a `parked` or not-yet-planned phase may have none.
   - Child status: `done` only when implemented and working; `in_progress` when partly built;
     `not_started` when planned; `parked` when on hold. A phase follows its children.
   - `depends_on` lists component ids needed first (existing ids, no cycles).
   - A component may hold its own `components:` list (any depth); ids stay unique across the project.
   - A big piece of work can be its own child project: the child carries `parent: <project-id>` and
     `under: <phase-or-component-id>` (an item in the parent, any depth). Only if the user agrees,
     create it with `workbench new "<Name>" --parent <id> --under <item-id>` or place an existing one
     with `workbench attach --id <child> --parent <id> --under <item-id>` (`workbench detach --id
     <child>` undoes it). One parent per project; record the child's detail in the child.
   - `summary` (≤300 chars): what it is, and for planned work why it is needed. `notes` (≤1000 chars):
     what is failing or blocking and the next concrete step. `items`: optional short sub-task checklist.
   - Project `status`: `planned` (not started), `active`, `paused`, `done`, `archived`; change it only
     if the user said so. A child project shows in the Projects tab only while `active`, otherwise
     only inside its parent. Keep `parent`/`under`. Do not edit `updated` or `origin`.
   - Learning paths may list `related_projects: [<project-id>]` (a reference only; no status flows).
   - Never include secrets, local paths, emails, private IPs, pasted logs, or personal information.
5. Run `workbench push <worktree> -m "<one-line summary>"`. If validation fails, fix the file and push
   again. If it reports removed ids or a large deletion, restore the missing items. Pass
   `--allow-removals` only if the user explicitly asked to remove something.
6. Report the one-line result, then list the `not_started` items you recorded so the user can correct
   them.
<!-- WORKBENCH:END -->
