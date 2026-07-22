# The Dispatcher — routing requests

Run this before executing anything. Not every request should be orchestrated.

## Classify

| Signal | Lane |
|---|---|
| Matches a defined workflow / recurring named action / correctness & traceability matter / same sources & outputs each time | **Rails** |
| Novel but multi-step, high-stakes, worth auditing | **Assisted** |
| Exploratory, one-off, open-ended, "help me think" | **Free** |

State your call to the user in one line. Ask a clarifying question **only when a wrong guess is costly** — cheap one-offs go Free without asking.

## Match against existing workflows

1. `... reindex` — rebuilds `workflows/index.yaml` from every workflow's front-matter and warns on duplicate names or overlapping triggers.
2. `... match "<the user's request>"` — returns candidate workflows scored by trigger/tag overlap.
3. Read the top candidates' `intent` lines and decide:
   - **One clear match** → propose it and echo-confirm before running. Never run silently (except scheduled tasks).
   - **Several plausible** → ask "did you mean X or Y?"
   - **Same workflow, different input** (Q2 vs Q3, client A vs B) → run the matched workflow with different `--param`, not a new file.
   - **No match** → treat as new: Free, or co-author a workflow if it's clearly repeatable.

## Lifecycle

- First time a repeatable task appears with no match → co-author a workflow (schema in `workflow-schema.md`), save to `workflows/`, `... reindex`.
- Every later run → load and follow (Rails).
- After a good Free/Assisted run → offer to save it as a workflow so it's repeatable next time.

The library grows only when structure is proven useful — never orchestrate preemptively.
