# ADR 007: Risk Tiers Enforced at the Executor, Not the Prompt

## Status
Accepted (2026-07-03, documented 2026-07-06)

## Context
FRIDAY's core trust property is "asks before doing anything risky, never
does the truly dangerous." The first instinct is to put that rule in the
system prompt: "always confirm before writing files, never delete."

Milestone 1 disproved that directly. Asked to clear a folder, the model
emitted `rm -rf ~/Downloads/*` through `run_shell` despite a prompt that
forbade it. Only the shell whitelist stopped it. A prompt is a request to
the model, not a constraint on it — under the right phrasing, load, or a
weaker provider, it will be ignored.

## Decision
Risk tiers are enforced in code, at the single choke point every tool call
passes through: `toolkit.execute()`. The model's cooperation is irrelevant
to safety.

- `app/core/tiers.py` is the visible, inspectable tier config. Every tool
  is listed with an explicit tier; a test (`test_every_registered_tool_has_
  an_explicit_tier`) fails the build if a new tool ships untiered.
- `Tier.NEVER` tools are refused even when `approved=True` is passed —
  defense in depth against a bug in the calling loop.
- `Tier.CONFIRM` tools are refused unless the loop passes `approved=True`,
  which only happens after the user types an approval word.
- Deletion has **no tool at all**. The safest capability is the one that
  doesn't exist.

The prompt still *describes* the tiers, but only so the model behaves
sensibly (doesn't ask permission in prose before calling a tool that will
trigger its own approval step). The prompt is UX; the executor is safety.

## Consequences
- Safety survives a compromised, confused, or swapped-out model.
- The tier map is auditable in one file — you can read exactly what FRIDAY
  will and won't do without tracing code.
- **Known limitation:** the gate is only as good as the classifier feeding
  it. `run_shell` classification parses the base command and pipe segments
  but not command substitution (`$(...)`, backticks), so a write-capable
  command smuggled inside substitution can currently be classified AUTO.
  See `docs/PERMISSIONS.md` → Known gaps. This is the same class of hole as
  the original whitelist leaks and needs the same treatment: close it in
  the classifier, verify with an adversarial test.
