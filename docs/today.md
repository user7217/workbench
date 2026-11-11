# Today's Work

**Today** is a tab of the dashboard that holds the part of the projects and learning paths chosen for the day. Each project or learning path with something chosen gets its own card, laid out like the Home grid. The cards are equals: work moves between projects freely, so nothing ranks one project above another. Inside a card the steps are ordered, and the card stands in for working memory when the project is picked back up: the step being done is marked **Now** in one colour, the step after it is marked **Next** in another, and finishing the current step moves the next one into its place.

---

## 1. Using It

**Adding work.** Every phase, topic, component and subtopic has a small sun button beside its status, and every project and learning path has a **Today** button in its header. Pressing one puts that item on today's list together with:

- everything nested under it, to any depth (including child projects attached under it), and
- every unfinished item those depend on (`depends_on`), even when it sits elsewhere in the project.

Only the chosen item is stored. Its parts are worked out from the current data each time, so a part that is added to a chosen topic later (by hand or by a coding agent) appears on the list by itself. Pressing the button again takes the item off.

**Working through it.** Each card shows, from top to bottom:

| Part | What it shows |
| :--- | :--- |
| Header | The project or learning path (a click opens it) and a button that takes the whole card off today's list. |
| Progress strip | One mark per step of that project, in order: done, now, next, or still to come. No numbers. |
| **Now** | The project's current step, where it sits in the project, its summary, how long it has been current on this device, and the buttons **Done**, **Start** (sets it in progress) and **Open**. |
| **Next** | The step after it, with **Do first** to swap. The swap sets that step in progress and, if the step it replaces was in progress, sets that one back to not started. |
| **Show every step** | The project's full ordered list, with done steps, on demand. |
| Chosen items | The items that were added from this project, each removable (not shown when the whole project was added). |

Every card has its own Now and Next, so several steps are current at once, one per project. Finishing a step in one card changes nothing in the others.

**Done** sets the item's status to done in its project, exactly like the status menu does, so nothing is tracked twice. **Clear finished** removes chosen items whose steps are all done; **Clear list** empties the tab. Neither changes any project.

**A new day.** On the first visit of a new day, if items with open steps are left over, the page asks which of them stay. Items that were finished are dropped without asking. Nothing is called overdue, and there are no streaks.

## 2. How the Order Is Worked Out

1. The chosen items are grouped by the project or learning path they belong to; groups keep the order their first item was added in. Within a group, each chosen item is expanded depth-first: a step comes **after** the parts nested under it and **after** everything it depends on, so a card's list can be worked from top to bottom.
2. A prerequisite outside the chosen item is pulled in just before the step that needs it and is labelled *needed before …*. A prerequisite that is already done is left out.
3. A step can be worked on when it is not done or parked, every part nested under it is finished, and everything it depends on is done. Other open steps are shown as **Waiting**.
4. In each group, **Now** is the first workable step that is already *in progress*; if none is, the first workable step. That is why **Start** and **Do this first** work by setting a status: the choice is saved with the project, is the same on every device, and a coding agent that marks an item in progress moves that project's Now step too.
5. **Next** is the step the same rule picks once the current step is done. Finishing the current step therefore always turns the step shown as Next into the current one.

The rules live in [`web/src/core/today.ts`](../web/src/core/today.ts) (`buildTodayPlan`), apart from any look, with tests beside the other core tests.

## 3. Where It Is Stored

The chosen items are kept in `today.yaml` at the root of `workbench-data` (format in [reference.md](reference.md)), so the list is the same on the server, the desktop app and a phone. Each change is one `edit(today): …` commit. Two things are kept only in the browser, per device: how long the current step has been current, and which prerequisites were seen unfinished today (so that finishing one shows it as done instead of making it vanish).

Every Workbench install has to be on a version that knows `today.yaml` before the tab is used; older versions reject the file as a disallowed path and stop syncing until they are updated.

## 4. Why It Looks the Way It Does

The tab follows a few principles from work on ADHD and executive function. The evidence is uneven, and it is stated here as it is:

| Principle | In the tab | Basis |
| :--- | :--- | :--- |
| **Put the information where the work happens.** Working memory is consistently weaker in ADHD, and the usual advice is to hold the plan outside the head, in view, at the moment of doing. | The order, the current step and the next step are always on screen; nothing has to be remembered between steps. | Russell Barkley's "externalise information at the point of performance", summarised by [ADDitude](https://www.additudemag.com/working-memory-powers-executive-function/). The working-memory deficit itself is well replicated; the advice built on it is clinical practice, not a tested product design. |
| **Cues help.** | Now and Next are cued by colour, label, size and position at once. | A [2022 study in children with ADHD](https://pubmed.ncbi.nlm.nih.gov/36514838) found that visual cues improved working-memory performance. It was a laboratory task, not task management. |
| **One thing at a time.** | Each project shows one current step with one primary button; its other steps are folded away until asked for. Which project to work on is left open on purpose, since that choice moves through the day. | Common guidance in ADHD-oriented tools such as [Super Productivity](https://super-productivity.com/for/adhd) (open source); design practice, without controlled trials. |
| **Make time visible.** | The Now card shows how long the step has been current. | Difficulty judging elapsed time is among the better-documented findings in ADHD; the display is a plain clock reading with no target attached. |
| **No punishment.** | No deadlines, counts, percentages or streaks; leftovers are asked about, not flagged. | Workbench's own principle (see the README), which matches the guidance above. |
| **Colour is never the only signal.** | Orange and sky blue stay apart under the common forms of colour blindness, and every state also has a text label. | Standard accessibility practice. |

No controlled study of a "now / next" task list for ADHD was found while building this, so the tab should be read as a design that follows the principles above, not as a validated intervention. A user-centred [design study with adults with ADHD](https://projekter.aau.dk/designing-a-digital-task-management-assistant-for-adults-with-adhd-a-user-centred-exploration-of-sch-bc52b1bf.html) draws the same line for its own prototype.

## 5. Turning It Off

Today's Work is on by default. It is switched off and on with the **Today's Work** switch in the **Settings** menu at the bottom of the dashboard's sidebar.

The switch is saved as `enabled` in `today.yaml` (an `edit(today): turn off` or `edit(today): turn on` commit), so it applies to every device and every server that reads the same `workbench-data`: the laptop, the desktop app, a Docker server and a phone all follow it once they have synced.

When it is off, the dashboard has no Today tab and no sun or **Today** buttons anywhere, and the server refuses changes to the list (`403`). Switching it back on is always allowed. The list is kept while it is off: the day and the entries stay in `today.yaml`, are still validated, and are shown again as they were once the switch is turned back on.
