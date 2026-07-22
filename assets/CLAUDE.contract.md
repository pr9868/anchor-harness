# Anchor harness — ground rules

This project uses the Anchor harness. When I ask you to do something:

1. **Dispatch first.** Decide if the request is *repeatable/rules-based* (Rails), *novel but multi-step* (Assisted), or *open-ended* (Free). State your call. Only ask me to clarify if guessing wrong would be costly. Don't orchestrate open-ended work.
2. **For a workflow**, match it against `workflows/index.yaml`, echo-confirm which one you're running, then follow it via the Anchor helper — never reorder or skip nodes, and let the helper's `ready` tell you what runs next.
3. **Run each node** at its declared autonomy, record its output, and **verify** it before advancing. Never claim a node passed without a successful verify.
4. **Stop at every blocking gate** and wait for my decision (auto-approve only `scheduled_ok` gates on scheduled runs).
5. **Same task, different input** = the same workflow with different `--param`, not a new file.
6. If any helper command returns `status: error`, **halt and show me the message** — never continue past it.
7. Default history is automatic file snapshots. Use git only if this project enables it. Sources authenticate via my connected tools; never store secrets in files.

Full behavior lives in the `anchor-orchestrator` skill.
