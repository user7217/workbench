<!-- WORKBENCH:START -->
## Workbench Project Tracking

The project's Workbench entry (`project.yaml`) is its long-term memory on the dashboard. There are two
modes. The Workbench AfterAgent hook asks for a **quick update** when files changed since the last
update, and for a **full review** when a new commit landed; otherwise it asks for nothing.

### Quick update (after each message)

Record only what this message changed, for the one item it worked on. Do not read or rewrite the whole
entry. Use one or two commands; each validates, privacy-scans, commits and pushes on its own.

1. Find the item. The hook reminder usually names the current focus (`project` or `project/item`). If
   not, or this message worked on something else:
   ```bash
   workbench focus                                 # current focus for this repo, if any
   workbench show --id <project> --brief           # one line per item: id, status, name
   workbench show --id <project> --item <item-id>  # just that item and its parts
   ```
   Inside a project's repo `--id` can be left out. Use the ids exactly as printed.
2. Record what changed with the smallest command that says it:
   ```bash
   workbench set <project> <item-id> --status done|in_progress|not_started|parked
   workbench set <project> <item-id> --notes "<current state and next step>"   # replaces the notes
   workbench add <project> --under <item-id> --name "<Name>" --status not_started --summary "<why>"
   ```
   `set` also takes `--name` and `--summary`; `add` also takes `--id` and `--notes`. Leave out
   `--under` on `add` only to create a new phase (or topic). Each command sets the repo's focus.
3. Or, if this message changed nothing worth recording (refactor, formatting, discussion that settled
   nothing), run `workbench hook-check --mark-seen` and reply "Workbench: no update needed".

Rules: `done` only when built and working (tests pass or the user saw it work); `in_progress` when
partly built; `not_started` for planned work; `parked` when the user put it on hold. New items only for
work the user agreed to or problems actually found, not your own ideas. Never rename or remove items
here; restructuring belongs in a full review. Never include secrets, API keys, local paths, emails,
private IPs, pasted logs or error output, or personal information. Commits are filed as
`update(<project>/<item>): ...` automatically. Reply with the one line each command printed.

### Full review (after a commit, at session end, or on request)

Reconcile everything this session built, finished, and left to do. The "left to do" part is the point
of the dashboard; never skip it. Also use this when creating a new project.

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
5. Run `workbench push <worktree> -m "<one-line summary>"`; the CLI files the commit as
   `review(<project>): <summary>`, so write only the summary. If validation fails, fix the file and push
   again. If it reports removed ids or a large deletion, restore the missing items. Pass
   `--allow-removals` only if the user explicitly asked to remove something.
6. Report the one-line result, then list the `not_started` items you recorded so the user can correct
   them.
<!-- WORKBENCH:END -->
