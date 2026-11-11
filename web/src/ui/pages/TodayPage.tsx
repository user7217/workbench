/**
 * Today's Work: one card per project or learning path that has something chosen for the day,
 * laid out like the Home grid. Work moves between projects freely, so the cards are equals and
 * nothing ranks one above another. Inside a card the steps are ordered: one is NOW and one is
 * NEXT, each in its own colour and with its own label, and finishing the NOW step moves NEXT into
 * its place. The card is there to stand in for working memory when a project is picked back up.
 *
 * Design choices and where they come from (docs/today.md has the sources):
 * - The order and the current step are on screen, not in the head (externalised working memory).
 * - One primary action per card, so there is nothing to choose between inside a project.
 * - Colour never carries meaning alone: NOW and NEXT also differ by label, size and position.
 * - Time on the current step is shown, without deadlines, counters, percentages or streaks.
 */
import { useEffect, useMemo, useState } from "react";
import {
  entryKey,
  entryLabel,
  localDay,
  unfinishedEntries,
  useWorkbench,
  type ItemStatus,
  type LearningItemStatus,
  type TodayEntry,
  type TodayGroup,
  type TodayPlan,
  type TodayStep,
} from "@workbench-core";
import { KindBadge, StatusNodeGlyph } from "../components/status";
import { TodayIcon } from "../components/TodayToggle";

const SEEN_KEY = "workbench.today.seen";
const SINCE_KEY = "workbench.today.since";

function readStored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback; // private window or blocked storage: the page works without it
  }
}

function writeStored(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // see readStored
  }
}

/**
 * The plan, plus memory of prerequisites seen unfinished today: without it a prerequisite would
 * vanish from the list the moment it is finished, instead of turning into a done step.
 */
function useTodayPlan(): TodayPlan {
  const wb = useWorkbench();
  const day = localDay();
  const [seen, setSeen] = useState<string[]>(() => {
    const stored = readStored<{ day: string; keys: string[] }>(SEEN_KEY, { day, keys: [] });
    return stored.day === day ? stored.keys : [];
  });
  const plan = useMemo(() => wb.todayPlan(new Set(seen)), [wb.todayPlan, seen]);

  useEffect(() => {
    const fresh = plan.groups
      .flatMap((g) => g.steps)
      .filter((s) => s.neededBy && s.state !== "done" && !seen.includes(s.key))
      .map((s) => s.key);
    if (fresh.length === 0) return;
    const keys = [...seen, ...fresh];
    setSeen(keys);
    writeStored(SEEN_KEY, { day, keys });
  }, [plan, seen, day]);

  return plan;
}

/**
 * Minutes each current step has been NOW on this device, by step key (absent until a minute has
 * passed). The start times live in the browser; steps that are no longer current are forgotten.
 */
function useMinutesOnSteps(stepKeys: string[]): Map<string, number> {
  const [, tick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(timer);
  }, []);
  const stored = readStored<Record<string, number>>(SINCE_KEY, {});
  const since: Record<string, number> = {};
  for (const key of stepKeys) since[key] = stored[key] ?? Date.now();
  if (JSON.stringify(since) !== JSON.stringify(stored)) writeStored(SINCE_KEY, since);
  const minutes = new Map<string, number>();
  for (const key of stepKeys) {
    const elapsed = Math.floor((Date.now() - since[key]) / 60_000);
    if (elapsed >= 1) minutes.set(key, elapsed);
  }
  return minutes;
}

function formatMinutes(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

function formatDay(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
}

/** Where a step sits inside its card's project: the items it is nested under. */
function stepContext(step: TodayStep, group: TodayGroup): string {
  const owner = step.ownerId === group.id && step.kind === group.kind ? [] : [step.ownerName];
  return [...owner, ...step.trail].join(" › ");
}

export function TodayPage() {
  const wb = useWorkbench();
  const { today } = wb;
  const plan = useTodayPlan();
  const day = localDay();
  const minutes = useMinutesOnSteps(plan.groups.flatMap((g) => (g.now ? [g.now.key] : [])));

  // A list confirmed on an earlier day is reviewed before it is worked on
  const stale = today.day !== null && today.day !== day && today.entries.length > 0;
  const leftovers = useMemo(() => unfinishedEntries(today, plan), [today, plan]);
  useEffect(() => {
    // Nothing unfinished was left: start the new day empty without asking
    if (stale && leftovers.length === 0) void wb.setTodayEntries([]);
  }, [stale, leftovers.length, wb.setTodayEntries]);

  return (
    <div className="today-page" data-testid="today-page">
      <header className="today-header">
        <div>
          <h1 className="today-title">
            <TodayIcon size={18} /> Today's Work
          </h1>
          <p className="today-date">{formatDay(day)}</p>
        </div>
        {today.entries.length > 0 && !stale && (
          <div className="today-header-actions">
            {leftovers.length < today.entries.length && (
              <button
                type="button"
                data-testid="today-clear-finished"
                onClick={() => void wb.setTodayEntries(leftovers)}
                title="Take the topics that are completely done off the list"
              >
                Clear finished
              </button>
            )}
            <button
              type="button"
              data-testid="today-clear-all"
              onClick={() => void wb.setTodayEntries([])}
              title="Empty Today's Work. Nothing in the projects changes."
            >
              Clear list
            </button>
          </div>
        )}
      </header>

      {stale && leftovers.length > 0 ? (
        <MorningReview
          fromDay={today.day as string}
          entries={leftovers}
          onConfirm={(keep) => void wb.setTodayEntries(keep)}
        />
      ) : today.entries.length === 0 ? (
        <EmptyToday />
      ) : (
        <>
          <div className="today-grid">
            {plan.groups.map((group) => (
              <GroupCard
                key={group.key}
                group={group}
                minutes={group.now ? (minutes.get(group.now.key) ?? null) : null}
              />
            ))}
          </div>
          {plan.missing.length > 0 && (
            <p className="today-missing">
              {plan.missing.length === 1
                ? "One entry points"
                : `${plan.missing.length} entries point`}{" "}
              at something that no longer exists.{" "}
              <button
                type="button"
                className="today-link-btn"
                onClick={() =>
                  void wb.setTodayEntries(
                    today.entries.filter(
                      (e) => !plan.missing.some((m) => entryKey(m) === entryKey(e)),
                    ),
                    false,
                  )
                }
              >
                Remove
              </button>
            </p>
          )}
        </>
      )}
    </div>
  );
}

/** One project or learning path: its current step, the one after it, and the rest on demand. */
function GroupCard({ group, minutes }: { group: TodayGroup; minutes: number | null }) {
  const wb = useWorkbench();
  const { today, snapshot } = wb;
  const [showAll, setShowAll] = useState(false);
  const { now, next } = group;
  const allDone = group.total > 0 && group.done === group.total;

  const setStatus = (step: TodayStep, status: ItemStatus) => {
    if (step.kind === "project") wb.setItemStatus(step.ownerId, step.itemId, status);
    else wb.setLearningItemStatus(step.ownerId, step.itemId, status as LearningItemStatus);
  };

  /** Put a step first: it becomes the one in progress, and the step it replaces is set back. */
  const startStep = (step: TodayStep) => {
    if (now && now.key !== step.key && now.status === "in_progress") setStatus(now, "not_started");
    setStatus(step, "in_progress");
  };

  const openStep = (step: TodayStep) => {
    if (step.kind === "project") {
      wb.openProjectAt(step.ownerId, step.itemId);
      return;
    }
    const path = snapshot.learning.find((lp) => lp.id === step.ownerId);
    const topic = path?.topics?.find(
      (t) => t.id === step.itemId || t.subtopics?.some((s) => s.id === step.itemId),
    );
    wb.selectLearningPath(
      step.ownerId,
      topic?.id,
      topic && topic.id !== step.itemId ? step.itemId : undefined,
    );
  };

  const openOwner = () =>
    group.kind === "project" ? wb.openProjectAt(group.id) : wb.selectLearningPath(group.id);

  const removeGroup = () =>
    void wb.setTodayEntries(
      today.entries.filter((e) => !(e.kind === group.kind && e.id === group.id)),
      false,
    );

  return (
    <article
      className={`today-group${allDone ? " is-done" : ""}`}
      data-testid={`today-group-${group.id}`}
    >
      <header className="today-group-header">
        <button
          type="button"
          className="today-group-title"
          onClick={openOwner}
          title={`Open ${group.name}`}
        >
          <KindBadge isLearning={group.kind === "learning"} status={group.status} size={28} />
          <span className="today-group-name">{group.name}</span>
        </button>
        <button
          type="button"
          className="today-source-remove"
          aria-label={`Take ${group.name} off Today's Work`}
          title="Take this off today's list"
          data-testid={`today-remove-${group.id}`}
          onClick={removeGroup}
        >
          ×
        </button>
      </header>

      <ProgressDots group={group} />

      {allDone ? (
        <div className="today-block today-block-done" data-testid={`today-all-done-${group.id}`}>
          <span className="today-all-done-mark" aria-hidden="true">
            ✓
          </span>
          <span>Everything chosen here is done.</span>
        </div>
      ) : now ? (
        <div
          key={now.key}
          className="today-block today-block-now"
          data-testid={`today-now-${group.id}`}
        >
          <div className="today-block-label">
            <span className="today-chip today-chip-now">Now</span>
            {minutes !== null && (
              <span
                className="today-elapsed"
                title="Time since this became the current step on this device"
              >
                on this for {formatMinutes(minutes)}
              </span>
            )}
          </div>
          <h2 className="today-block-title">{now.name}</h2>
          {stepContext(now, group) && (
            <p className="today-block-context">{stepContext(now, group)}</p>
          )}
          {now.summary && <p className="today-block-summary">{now.summary}</p>}
          {now.neededBy && <p className="today-block-note">Needed before: {now.neededBy}</p>}
          {now.hasParts && (
            <p className="today-block-note">
              Its parts are done. Check the whole and mark it done.
            </p>
          )}
          <div className="today-block-actions">
            <button
              type="button"
              className="today-done-btn"
              data-testid={`today-done-${group.id}`}
              onClick={() => setStatus(now, "done")}
            >
              Done
            </button>
            {now.status !== "in_progress" && (
              <button
                type="button"
                data-testid={`today-start-${group.id}`}
                onClick={() => startStep(now)}
                title="Mark this step as in progress"
              >
                Start
              </button>
            )}
            <button type="button" onClick={() => openStep(now)}>
              Open
            </button>
          </div>
        </div>
      ) : (
        <div className="today-block today-block-idle">
          <span className="today-chip">Nothing to do right now</span>
          <p className="today-block-note">
            The steps left are parked, or wait on something that is parked.
          </p>
        </div>
      )}

      {!allDone && next && (
        <div
          key={next.key}
          className="today-block today-block-next"
          data-testid={`today-next-${group.id}`}
        >
          <div className="today-next-text">
            <span className="today-chip today-chip-next">Next</span>
            <span className="today-next-name">{next.name}</span>
          </div>
          <button
            type="button"
            data-testid={`today-swap-${group.id}`}
            onClick={() => startStep(next)}
            title="Make this the current step instead"
          >
            Do first
          </button>
        </div>
      )}

      <button
        type="button"
        className="today-all-toggle"
        aria-expanded={showAll}
        data-testid={`today-all-${group.id}`}
        onClick={() => setShowAll((v) => !v)}
      >
        {showAll ? "Hide the steps" : "Show every step"}
      </button>
      {showAll && (
        <ol className="today-steps">
          {group.steps.map((step) => (
            <StepRow
              key={step.key}
              step={step}
              context={stepContext(step, group)}
              onDone={() => setStatus(step, "done")}
              onStart={() => startStep(step)}
              onUndo={() => setStatus(step, "not_started")}
              onOpen={() => openStep(step)}
            />
          ))}
        </ol>
      )}

      {!group.whole && (
        <ul className="today-source-list" aria-label={`Chosen from ${group.name}`}>
          {group.entries.map((entry) => (
            <SourceChip
              key={entryKey(entry)}
              entry={entry}
              onRemove={() => void wb.removeFromToday(entry)}
            />
          ))}
        </ul>
      )}
    </article>
  );
}

/** One mark per step of a card, in order: done, now, next or still to come. No numbers on purpose. */
function ProgressDots({ group }: { group: TodayGroup }) {
  const counted = group.steps.filter((s) => s.state !== "parked");
  if (counted.length === 0) return null;
  return (
    <div
      className="today-dots"
      role="img"
      aria-label={`${group.done} of ${group.total} steps done`}
      data-testid={`today-dots-${group.id}`}
    >
      {counted.map((s) => (
        <span key={s.key} className={`today-dot today-dot-${s.state}`} title={s.name} />
      ))}
    </div>
  );
}

const STATE_LABEL: Partial<Record<TodayStep["state"], string>> = {
  now: "Now",
  next: "Next",
  blocked: "Waiting",
  parked: "Parked",
};

function StepRow({
  step,
  context,
  onDone,
  onStart,
  onUndo,
  onOpen,
}: {
  step: TodayStep;
  context: string;
  onDone: () => void;
  onStart: () => void;
  onUndo: () => void;
  onOpen: () => void;
}) {
  const label = STATE_LABEL[step.state];
  const canAct = step.state === "now" || step.state === "next" || step.state === "later";
  return (
    <li
      className={`today-step today-step-${step.state}`}
      data-testid={`today-step-${step.itemId}`}
      data-state={step.state}
    >
      <span className="today-step-glyph">
        <StatusNodeGlyph status={step.status} size={15} />
      </span>
      <button
        type="button"
        className="today-step-main"
        onClick={onOpen}
        title="Open in its project"
      >
        <span className="today-step-name">{step.name}</span>
        {(context || step.neededBy) && (
          <span className="today-step-context">
            {[context, step.neededBy ? `needed before ${step.neededBy}` : ""]
              .filter(Boolean)
              .join(" · ")}
          </span>
        )}
      </button>
      <span className="today-step-actions">
        {canAct && step.state !== "now" && (
          <button type="button" onClick={onStart} title="Make this the current step">
            Do first
          </button>
        )}
        {canAct && (
          <button type="button" onClick={onDone} title="Mark done">
            Done
          </button>
        )}
        {step.state === "done" && (
          <button type="button" onClick={onUndo} title="Set back to not started">
            Not done
          </button>
        )}
      </span>
      {label && <span className={`today-chip today-chip-${step.state}`}>{label}</span>}
    </li>
  );
}

function SourceChip({ entry, onRemove }: { entry: TodayEntry; onRemove: () => void }) {
  const { snapshot } = useWorkbench();
  const label = entryLabel(snapshot, entry);
  if (!label) return null;
  return (
    <li className="today-source">
      <span className="today-source-name">{label.item ?? label.owner}</span>
      <button
        type="button"
        className="today-source-remove"
        aria-label={`Remove ${label.item ?? label.owner} from Today's Work`}
        title="Take off today's list"
        onClick={onRemove}
      >
        ×
      </button>
    </li>
  );
}

/** First visit on a new day with unfinished topics left over: choose what stays. */
function MorningReview({
  fromDay,
  entries,
  onConfirm,
}: {
  fromDay: string;
  entries: TodayEntry[];
  onConfirm: (keep: TodayEntry[]) => void;
}) {
  const { snapshot } = useWorkbench();
  const [dropped, setDropped] = useState<Set<string>>(new Set());
  const kept = entries.filter((e) => !dropped.has(entryKey(e)));
  return (
    <section className="today-review" data-testid="today-review">
      <h2>New day. What stays on the list?</h2>
      <p>
        These topics were on the list on {formatDay(fromDay)} and still have open steps. Untick
        anything that is not for today; it stays in its project either way.
      </p>
      <ul className="today-review-list">
        {entries.map((entry) => {
          const key = entryKey(entry);
          const label = entryLabel(snapshot, entry);
          if (!label) return null;
          return (
            <li key={key}>
              <label>
                <input
                  type="checkbox"
                  checked={!dropped.has(key)}
                  onChange={(e) =>
                    setDropped((prev) => {
                      const next = new Set(prev);
                      if (e.target.checked) next.delete(key);
                      else next.add(key);
                      return next;
                    })
                  }
                />
                <span className="today-source-name">{label.item ?? label.owner}</span>
                {label.item && <span className="today-source-owner">{label.owner}</span>}
              </label>
            </li>
          );
        })}
      </ul>
      <div className="today-card-actions">
        <button
          type="button"
          className="today-done-btn"
          data-testid="today-review-keep"
          onClick={() => onConfirm(kept)}
        >
          {kept.length === 0
            ? "Start empty"
            : kept.length === entries.length
              ? "Keep all"
              : "Keep ticked"}
        </button>
        {kept.length > 0 && (
          <button type="button" data-testid="today-review-empty" onClick={() => onConfirm([])}>
            Start empty
          </button>
        )}
      </div>
    </section>
  );
}

function EmptyToday() {
  const { navigate } = useWorkbench();
  return (
    <section className="today-empty" data-testid="today-empty">
      <h2>Nothing is on today's list yet.</h2>
      <p>
        Open a project or a learning path and press the{" "}
        <span className="today-inline-icon">
          <TodayIcon size={13} />
        </span>{" "}
        beside a phase, a topic or any part. It comes here with everything nested under it and
        anything unfinished it depends on, in the order it can be worked through.
      </p>
      <div className="today-card-actions">
        <button type="button" onClick={() => navigate({ page: "building" })}>
          Go to projects
        </button>
        <button type="button" onClick={() => navigate({ page: "learning" })}>
          Go to learning paths
        </button>
      </div>
    </section>
  );
}
