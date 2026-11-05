# Workbench learning-path chat prompt

Paste everything below the line into the chat that holds what you have been learning. Replace the
CURRENT OUTLINE placeholder first:

- existing path: the output of `workbench learning export <path-id>`
- new path: `NEW: <Path Name>`

Save the reply's outline as a `.md` file and run `workbench learning import <file>.md`. Importing
into an existing path merges: statuses only move forward, and items missing from the outline are
kept, so progress marked on the dashboard is never lost.

---

Turn what this conversation shows about my learning into a Workbench learning-path outline: what I
set out to learn, what I have covered, what I am partway through, and what is still left. The "still
left" part matters most; do not skip it.

CURRENT OUTLINE:
<paste `workbench learning export <path-id>` output here, or write NEW: <Path Name>>

Reply with exactly one ```markdown code block and nothing else — no text before or after it.

FORMAT:

```markdown
# Control Theory
Feedback control from first principles, aimed at steering a field robot along crop rows.
- [Feedback Systems by Astrom and Murray](https://fbswiki.org/wiki/index.php/Main_Page)

## [x] Linear systems
State-space models and what decides whether a system settles.

### [x] State-space models
Writing dynamics as x' = Ax + Bu.

### [~] Stability and eigenvalues
Eigenvalues of A in the left half-plane mean the system settles.

## [ ] PID control
The controller used on the robot's steering loop.

### [ ] Tuning
Choosing gains from a step response.

### [ ] Anti-windup
Stopping the integral term from growing while the actuator is saturated.
```

RULES:

1. Structure: one `#` line with the path name, then `##` topics, each with `###` subtopics. Nothing
   deeper than `###`.
2. Status marker right after the hashes on every `##` and `###` line: `[x]` covered and understood,
   `[~]` started but not finished, `[ ]` not started. Mark `[x]` only when the conversation shows I
   actually worked through it.
3. Keep every topic and subtopic from CURRENT OUTLINE with exactly the same heading text, including
   any `{#id}` at the end of a heading. You may change its marker and summary, and add new items.
   Never rename or remove one.
4. Still left → `[ ]` items: what I said I want to learn next, prerequisites we found I am missing,
   and topics we deferred. Do not add topics I did not ask for or agree to.
5. Under any heading you may add one line of summary (≤300 characters; under `#`, ≤500) and links as
   `- [Label](https://...)` lines. Labels ≤40 characters; only `https://` links. Leave them out if
   you have nothing real to put there — never invent a link.
6. Heading names ≤80 characters. Two items must not have the same name.
7. Never include secrets, passwords, local file paths, email addresses, or personal information.
8. If something is unclear from the conversation, leave it out instead of guessing.
