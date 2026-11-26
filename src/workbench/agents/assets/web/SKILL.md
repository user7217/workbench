---
name: workbench-web
description: Record progress from this chat on the Workbench dashboard via the Workbench connector - a status, notes, a new item or a suggestion. Use when a chat finishes, starts or blocks project or study work.
---

# Workbench from a chat: record what this conversation established

The owner keeps a dashboard of software projects and learning paths. The **Workbench** connector
gives you a few tools to read it and to record progress. Use them when this conversation
finished, started or uncovered something on a project or learning path the dashboard tracks:
a feature now works, a bug turned up, a plan was agreed, a topic was understood.

Do not record a chat that only discussed ideas, asked a general question or settled nothing.
When unsure whether something is worth recording, ask the owner in one short sentence.

## 1. Find the item

```
list_projects()                    → every project and learning path: id, name, status
show_project(project_id)           → the project's outline: [status] name (id): summary, notes
show_learning_path(path_id)        → topics and subtopics with status and id
```

Use the ids exactly as printed. If the conversation does not make clear which project or item
it was about, ask the owner instead of guessing.

## 2. Record the smallest change that says it

```
set_item_status(project_id, item_id, status, notes?)   → it moved: done, started, parked
update_item_notes(project_id, item_id, notes?, summary?) → its current state changed
add_item(project_id, name, parent_id?, summary?, status?) → a new problem or an agreed next step
suggest_item(project_id, name, summary?, under?)       → your own idea, for the owner to review
set_learning_item_status(path_id, item_id, status)
update_learning_item_notes(path_id, item_id, notes?, summary?)
```

Usually one call is enough, two at most. Each write is committed on the dashboard on its own,
labelled `web(...)`, and can be undone there.

- **Notes** say the current state in a sentence or two: what works, what is failing or blocking,
  the next step. They replace the old notes, so keep anything still true.
- **Build the tree, don't flatten it.** Give a sub-task the `parent_id` of the item it belongs
  to, so items gain children as work is broken down (parts of parts are fine). Leave `parent_id`
  out only to create a genuinely new phase or topic.
- **Your own ideas are suggestions, not items.** Follow-ups, options or recommendations you came
  up with go through `suggest_item` (into the project's inbox, where the owner keeps or dismisses
  them), unless the owner said in this chat that they want them tracked; then use `add_item`.

## 3. Tell the owner

Reply with one line per change, as the tool reported it (for example "Set robot-arm/gripper to
done"). If a tool returns an error, say what it said; do not retry with invented ids.

## Rules

- **Statuses**: `done` only when built and working (tests pass or the owner saw it work);
  `in_progress` when partly built; `not_started` for planned work; `parked` when the owner put
  it on hold. For learning items, `done` when the owner can explain or use it. Set the item that
  was worked on, not its parents: a parent turns done by itself when its last part does.
- **Only what happened**: record what this conversation established, not plans nobody agreed to
  or work that was only talked about.
- **No renames, moves or deletions**: the connector cannot do them; suggest them to the owner
  instead.
- **Privacy**: never put secrets, API keys, tokens, passwords, local file paths, email addresses,
  private IP addresses, pasted logs or error output, or personal information about anyone into a
  name, summary or note. Describe the problem in your own words instead.
