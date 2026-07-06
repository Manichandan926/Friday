# FRIDAY Permission Tiers

The permission system is the reason you can let FRIDAY act on your machine
without watching it. This document explains the model, where each tool sits
and why, how to add a new tool correctly, and — honestly — where the model
is currently weak.

Design rationale (why tiers live in the executor, not the prompt) is in
`docs/adr/ADR-007-tiers-at-executor.md`. This is the operational reference.

## The three tiers

Defined in `app/core/tiers.py` as `Tier(IntEnum)`:

| Tier | Name | Meaning | Examples |
|------|------|---------|----------|
| 1 | `AUTO` | Low risk, reversible. Runs immediately, always audit-logged. | reading system stats, listing tasks, saving a note or memory, watching a folder |
| 2 | `CONFIRM` | Medium risk. Proposed to you; runs **only** after you approve. | writing a file, creating a directory, a write-capable shell command |
| 3 | `NEVER` | High risk / hard to undo. Refused **always**, even if approval is somehow passed. | deletion, `sudo`, disk writes, anything not on the shell whitelist |

Two principles make this trustworthy:

1. **Enforcement is in code, at one choke point.** Every tool call goes
   through `toolkit.execute()`, which classifies it and enforces the tier.
   The system prompt describes the tiers so the model behaves sensibly, but
   the prompt is not what stops anything. A confused, jailbroken, or swapped
   model still can't cross a tier.
2. **The most dangerous capabilities don't exist as tools.** There is no
   delete tool. NEVER isn't "a tool we refuse to run" so much as "a line we
   didn't build a door in." Deletion is a NEVER *classification* for shell
   commands, and simply an absent capability everywhere else.

## How a call is tiered

For most tools the tier is a static lookup in `TOOL_TIERS`. Two things are
dynamic:

- **`run_shell`** is classified per command by `classify_command()`:
  - not on the whitelist, or matches a blocked pattern (`rm`, `sudo`, `dd`,
    fork bombs, …) → **NEVER**
  - whitelisted but write-capable — any `>`/`>>` redirect, `tee`, or a base
    command in `WRITE_CAPABLE` (`touch`, `mkdir`, `git`, `pip`, `python`,
    `systemctl`, …) → **CONFIRM**
  - whitelisted and read-only → **AUTO**
- **Unlisted tool names** default to `CONFIRM` — the safe default, so a tool
  that somehow reaches the gate without a tier is held for approval rather
  than run.

## The approval flow

When the loop hits a CONFIRM call it doesn't execute. It stashes the held
call(s) and returns a proposal:

```
⏸ Approval needed — I want to:
  1. write_file: ~/notes/todo.md
Reply "yes" to approve, anything else to skip.
```

Your next message resolves it. An approval word (`yes`, `ok`, `approve`,
`go ahead`, `do it`, `sure`, `/approve`, …) runs the held calls with
`approved=True`. Anything else skips them and tells the model you declined;
if what you typed wasn't a bare "no", it's also passed along as a new
request. Approval is **batch, all-or-nothing** per round.

> ⚠️ The approval-word list is an exact-match set. `yes.` `yeah` and
> `Yes, go ahead` are **not** currently recognized as approval — they skip
> the action. This is a known rough edge; see Known gaps.

## Adding a new tool — do this, in order

There is a **build-time guardrail**: `tests/test_tiers.py::
test_every_registered_tool_has_an_explicit_tier` fails if any registered
tool is missing from `TOOL_TIERS`. You cannot ship an untiered tool by
accident — the suite goes red. Steps:

1. **Register the tool** in `app/core/toolkit.py` with `@_register(name,
   description, params, required)`. The description is what the model sees —
   be precise about side effects.
2. **Assign a tier** in `app/core/tiers.py`'s `TOOL_TIERS`. Ask: is this
   reversible and low-harm (AUTO), a change the user should confirm
   (CONFIRM), or something that should never run unattended (NEVER — and
   then reconsider whether it should exist as a tool at all)?
3. **Confine side effects.** A write tool must resolve and bound its paths
   (see `_safe_write_path` / `ALLOWED_WRITE_ROOTS`), not trust its argument.
4. **Write an adversarial test**, not just a happy-path one. The tier system
   earned trust by being attacked: the whitelist was tested with real `rm`
   attempts, `write_file` with paths outside home. Test that your tool
   refuses what it should refuse.
5. Run the suite. Green includes the guardrail above.

## Known gaps (as of Phase 1 completion)

Stated plainly, because a permission doc that hides holes is worse than
none. These are real and confirmed by testing, not hypotheticals.

- **Command substitution bypasses the tier classifier.** `classify_command`
  parses the base command and pipe segments but not `$(...)` or backticks.
  A write-capable command smuggled inside substitution is classified AUTO
  and runs with no approval — e.g. `echo $(python3 -c "...")` executes
  arbitrary Python at Tier 1. This is the same class of hole as the original
  whitelist leaks and needs the same fix: detect substitution in the
  classifier (treat any command containing `$(`, `` ` ``, or `${` as at
  least CONFIRM, ideally NEVER), backed by an adversarial test. **Highest-
  priority hardening item.**
- **`watch_directory` has no path scope.** It can watch any readable
  directory (`/etc`, `~/.ssh`), unlike `write_file` which is confined to
  `ALLOWED_WRITE_ROOTS`. It's read-only observation so lower severity, but
  it's an information-gathering capability at AUTO with no boundary —
  inconsistent with the rest of the design. Consider a watch allow-list.
- **Approval matching is brittle** (see the warning above). Natural
  confirmations fail closed (the action is skipped, not wrongly run), so
  this is a trust/UX problem, not a safety one — but for a system whose
  whole pitch is "asks before acting", the asking should actually work on
  normal human phrasing.
