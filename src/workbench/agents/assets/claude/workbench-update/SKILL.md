---
name: workbench-update
description: Quick per-message update of the Workbench dashboard - record what this message changed for the one item being worked on (a status, a note, a newly found problem). Use when a Workbench hook reminder asks for a quick update, or after a message that finished, started, or broke something. For a full reconcile of the whole entry, use workbench-review.
---

# Workbench update: record this message, scoped to one item

The dashboard should change a little after every message: something got done, something started,
or a new problem turned up. This skill records **only that**, for the item this message worked on.
Do not read or rewrite the whole entry — that is what **workbench-review** is for.

Keep it to one or two commands. Each one validates, privacy-scans, commits and pushes on its own.

## 1. Find the item

The hook reminder usually names the current **focus** (`project` or `project/item`). If it does not,
or this message worked on something else:

```bash
workbench focus                                 # current focus for this repo, if any
workbench show --id <project> --brief           # one line per item: id, status, name
workbench show --id <project> --item <item-id>  # just that item and its parts
```

Inside a project's repo `--id` can be left out. Use the ids exactly as printed.

## 2. Record what changed

Pick the smallest command that says it:

```bash
# something moved: done, started, parked
workbench set <project> <item-id> --status done|in_progress|not_started|parked

# the current state changed (what is failing or blocking, the next step) — replaces the notes
workbench set <project> <item-id> --notes "Overshoots above 1 m/s; next: tune lookahead in a field test"

# a new problem or a newly agreed next step — a new item under an existing phase or component
workbench add <project> --under <item-id> --name "Fix Float timeout in rtk_sim" --status not_started \
  --summary "rtk_sim never drops to Float, so the safety monitor's timeout cannot be tested"

# link items: this one needs others first (component ids, comma-separated; set replaces the list)
workbench add <project> --under <item-id> --name "Field coverage run" --depends-on headland-turns,safety-monitor
workbench set <project> <item-id> --depends-on headland-turns
```

**Build the tree, don't flatten it.** A sub-task goes `--under` the item it belongs to, so items
keep gaining children as work is broken down (a part of a part is fine, to any depth). Add a new
top-level phase only for genuinely new work.

`--name` and `--summary` can also be passed to `set`. Leave out `--under` on `add` only to create a
new phase (or topic). Each command remembers its project/item as the focus for this repo, so the
next reminder names it for you.

## 3. Let the user pick from your suggestions

If this message ended with recommendations, follow-ups, or options for the user that are not
already on the dashboard, do not add them silently. Ask with the **AskUserQuestion** tool,
`multiSelect: true`, one option per suggestion: a short label (1–5 words) and a one-line
description. Use up to 4 options per question and up to 4 questions; if there are more
suggestions, group related ones or ask about the most important.

Then add **only the selected ones**, each in the right place:
- `--under` the existing item it belongs to (check `workbench show --brief`), so the tree gains
  depth instead of a flat list;
- `--depends-on` the items it needs first, whether they already existed or were just selected
  (add those first so their ids exist).

Unselected suggestions were declined: do not add them. If the AskUserQuestion tool is not
available, list the suggestions numbered, ask which to track, and wait for the answer.

## 4. Or nothing

If this message changed nothing worth recording (a refactor, formatting, discussion that settled
nothing), run `workbench hook-check --mark-seen` and reply `Workbench: no update needed`.

## Rules

- **Statuses**: `done` only when built and working (tests pass or the user saw it work);
  `in_progress` when partly built; `not_started` for planned work; `parked` when the user put it on hold.
- **New items**: problems actually found, next steps the user asked for or agreed to, and
  suggestions the user selected in step 3. Your own suggestions go through step 3, never straight in.
- **Never** rename or remove items here; restructuring belongs in workbench-review.
- **Privacy**: no secrets, API keys, local paths (`/Users/...`, `~/...`), emails, private IPs,
  pasted logs or error output, or personal information.
- Commits are filed as `update(<project>/<item>): ...` automatically.
- Then reply with the one line each command printed.
