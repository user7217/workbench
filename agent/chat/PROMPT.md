# Workbench chat prompt

Paste everything below the line into the chat that holds the project's history (claude.ai, the
desktop app, or any other assistant). Replace the CURRENT ENTRY placeholder first:

- existing project: the output of `workbench show --id <project-id>`
- new project: `NEW: <Project Name>`

Then put the reply into Workbench with `workbench begin --id <project-id>`: replace the printed
`project.yaml` with the reply's YAML and run `workbench push <worktree> -m "<summary>"`. Push
validates the format, scans for private data, and refuses removed ids.

---

Update my Workbench dashboard entry for the project described in this conversation. Use everything
this conversation shows about it: what was built, what is in progress, what was decided, what is
still failing, and what is left to do. The "left to do" part matters most; do not skip it.

CURRENT ENTRY:
<paste `workbench show --id <project-id>` output here, or write NEW: <Project Name>>

Reply with exactly one ```yaml code block and nothing else — no text before or after it.

FORMAT (keys in this order):

```yaml
schema_version: 1
id: field-robotics            # keep the current id; for NEW, a slug of the name
name: Field Robotics          # ≤80 chars
description: >-               # ≤500 chars: what the project is and what it is for
  Autonomous field robot for row-crop scouting: localization, navigation and mapping
  on a ROS 2 stack, plus the tooling around it.
status: active                # planned | active | paused | done | archived — keep current unless I said otherwise
uses_phases: true
repos: []                     # copy from the current entry unchanged
links: []                     # copy unchanged; you may add {label: ≤40 chars, url: https://...}
phases:
  - id: localization
    name: Localization
    status: done              # not_started | in_progress | done | parked
    summary: GPS plus wheel odometry fused into a single pose estimate.
    components:
      - id: gps-driver
        name: GPS driver
        status: done
        summary: Reads RTK fixes and publishes them in the map frame.
        components:           # optional: a component's own parts, nested to any depth
          - id: rtk-corrections
            name: RTK corrections
            status: done
            summary: Streams base-station corrections to the receiver.
      - id: pose-fusion
        name: Pose fusion
        status: done
        summary: Fuses GPS and odometry into one pose.
        depends_on: [gps-driver]
  - id: navigation
    name: Navigation
    status: in_progress
    summary: Following crop rows and turning at headlands.
    components:
      - id: row-following
        name: Row following
        status: in_progress
        summary: Pure-pursuit controller along detected rows.
        notes: Overshoots at speeds above 1 m/s; gains not yet tuned in the field.
        depends_on: [pose-fusion]
        items:
          - Tune lookahead distance in a field test
      - id: headland-turns
        name: Headland turns
        status: not_started
        summary: Turn-around at row ends, needed before full-field runs.
        depends_on: [row-following]
topics: []
```

RULES:

1. Merge, do not regenerate. Keep every phase and component already in CURRENT ENTRY with the same
   `id`; update its status and text, and add new ones. Never rename or drop an `id`. If the entry
   uses `uses_phases: false` with `topics:` instead of `phases:`, keep that structure.
2. Status: `done` only when it is built and working; `in_progress` when partly built or not yet
   working; `not_started` when planned; `parked` when put on hold. A phase is `done` when all its
   components are, `in_progress` when any has started.
3. Left to do → `not_started` components: things I said should happen next or later, work that was
   deferred or split off, known problems that were not fixed, and follow-ups I agreed to. Do not add
   ideas I did not agree to.
4. Every phase with real work — done, in progress, or planned — has components. Only a `parked` or
   not-yet-planned phase may have none.
5. Ids: lowercase letters, digits and single hyphens (`row-following`), at most 48 characters,
   unique across the whole entry.
6. `depends_on` only lists ids of other components in this entry, and must not form a cycle.
7. `summary` ≤300 chars (what it is; for planned work, why it is needed). `notes` ≤1000 chars
   (what is failing or blocking, and the next step). `items` is an optional checklist, each ≤300
   chars. Leave out any optional field you have nothing for.
8. Do not include `updated` or `origin`; Workbench sets them.
9. Never include secrets, API keys, passwords, local file paths, email addresses, IP addresses,
   pasted logs or error output, or personal information about anyone.
10. If something is unclear from the conversation, leave it out instead of guessing.
11. A component can list its own `components:` (to any depth) when it is made of smaller parts;
    ids stay unique across the whole entry, nested ones included.
12. If CURRENT ENTRY has `parent:` and `under:` lines, this project is a child of another project;
    copy both unchanged. Do not add them yourself — making a project a child of another is done
    with `workbench new "<Name>" --parent <id> --under <item-id>` or `workbench attach`, and only
    when I ask. A project has at most one parent. Project `status: planned` means not started yet;
    a child project shows in the Projects tab only while `active`, otherwise only inside its parent.
