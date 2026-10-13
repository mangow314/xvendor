---
name: xvendor
description: Send read-only jobs to other vendors' official headless CLIs (codex exec, agy -p) and ask follow-ups — a cross-vendor code review, a second opinion on a plan, or having codex or agy read code and report back. One command starts a job, a tmux pane shows its progress live, the result lands in a job directory, and follow-up rounds resume the same conversation; xv enforces read-only. Triggers: cross-vendor, codex review, ask codex, ask agy, second opinion from codex or agy, xv. Not for: work where the other model must edit files (always read-only), talking to an interactive codex or agy session the user opened by hand, or messages between Claude sessions.
---

# xvendor

`xv` is the single entry point for cross-vendor jobs. It runs codex or agy headless and read-only, streams what they do into a tmux pane, and writes the request, the event stream and the final answer into a job directory.

Call `xv` as a command of its own: no `cd`, `;`, `&&` or `|` around it. If Claude Code's Bash sandbox is on, `xv` has to run outside it (it writes the job directory, opens tmux panes, and codex and agy need the network), so the user lists it in `sandbox.excludedCommands` (for example `"xv *"`). A chained command does not match that entry, stays in the sandbox, and fails with `Read-only file system`. Read results with the Read tool on `response.md`, or with `xv show`.

## When to use

- One cross-vendor review round (codex and agy each review once; compare the findings).
- A second opinion on a plan or design.
- Having codex or agy read code, check something, and report back.

All of these are **read-only**:

- codex runs with `-s read-only`, and follow-up rounds pass `sandbox_mode="read-only"` again (a resumed codex session does not keep the first round's flag).
- agy runs inside a bubblewrap (`bwrap`) sandbox with the whole filesystem mounted read-only; on the host only agy's own state directory is writable, plus a per-round temp directory deleted afterwards. `/run/user/<uid>` and the default tmux socket directory are covered by empty folders, so agy cannot reach `systemd --user` or the default tmux server to run commands outside the sandbox (a tmux server on a custom `-S` socket elsewhere is not hidden). Credential files in the home directory (`~/.ssh`, `~/.aws`, `~/.codex/auth.json` and others) are blanked out. Where this is not possible (macOS, or no `bwrap`), `xv start agy` refuses to run; if agy's state directory is incomplete, the round fails, and the tmux pane shows what to do. agy's own `--sandbox` flag does not stop its file-writing tools, so xv does not rely on it.

Do not use this skill for work where the other model has to change files.

## Commands

```bash
xv start <codex|agy> --file brief.md [--cwd DIR] [--timeout SEC] [--no-pane]   # prints the job id
xv start <codex|agy> -m "short request"
xv start codex --profile NAME -m "..."   # uses $CODEX_HOME/NAME.config.toml (default ~/.codex)
xv wait  <job-id> [--timeout SEC]        # blocks until the round is no longer running; rc 0 = done
xv show  <job-id> [ROUND]                # prints response.md (latest round by default)
xv ask   <job-id> --file F | -m TEXT [--timeout SEC]   # follow-up: resumes the same conversation in the same pane
xv stop  <job-id>                        # kills the current round (the session is kept), then xv ask to redirect
xv list                                  # every job with vendor, round, status and seconds
xv close <job-id>                        # closes the job's pane (run once per job when done)
xv watch <job-id>                        # follows the current round from any terminal (for the user)
```

`<job-id>` can also be its last 4 characters (the pane ends with `xv show 3949`); it is accepted only when it matches exactly one job. The pane lists actions as they happen and prints the answer once, as Markdown, when the round ends. Malformed input (job id, round, `--timeout`, `--profile`) always gives `xv: …` and rc 2.

- Inside tmux: `start` opens a pane beside the caller (without taking focus) that shows the event stream; `ask` reuses the same pane. Outside tmux, or with `--no-pane`: the round runs in the background.
- `start --timeout` defaults to 1200 seconds; `ask` keeps the job's timeout unless given one, and `wait` waits that long plus 60 seconds unless given `--timeout`. In testing, an agy review of one 1100-line file took 739 seconds and codex took 92; give agy 1800 for wide tasks.
- `xv wait` blocks. A single Bash call is capped at 10 minutes, so use `run_in_background` for longer waits, or wait in steps with `xv wait <job-id> --timeout 540`.

## Job directory

```
${XV_DATA:-~/.local/share/xvendor}/jobs/<job-id>/
|-- meta.json            vendor, cwd, session id, round, status, rc, secs, pane, run_pid, run_pgid
`-- round-N/
    |-- request.md       the request that was sent
    |-- response.md      the other model's final answer, taken from the event stream
    |-- events.jsonl     the full event stream
    `-- stderr.log
```

status is one of `running`, `done`, `failed`, `timeout`, `stopped`. When reporting to the user or citing a result elsewhere, give the job id and the path to `response.md`.

## Writing the brief

1. Make the brief self-contained: the other model cannot see this session. State what to review (absolute paths), what to look for, what to leave out, and the output format (for example `[severity] file:line — problem`, plus the scenario that triggers it).
2. For a cross-vendor review, start both vendors in parallel (one `xv start` each), then `xv wait` on each. Do not show one vendor's conclusions to the other before both have given their first answer, or the two reviews stop being independent. Tell each one not to read anything under `~/.local/share/xvendor/`: the other vendor's job directory is readable to a read-only worker.
3. Ask follow-ups with `xv ask`, not a new `start`: only a resumed conversation keeps the previous round's context.
4. For long tasks (more than about 5 minutes, common for agy reviews), work in two steps: in round 1 ask only for the scope and steps it plans, without starting; read them, then `xv ask` it to go ahead.
5. To pull a job back on course: `xv stop <job>`, `xv wait <job>`, then `xv ask <job> -m "Stop; look at X instead"`. A stopped round's progress is kept when the conversation resumes. If the round is stopped before the vendor reported a session id, the job cannot be asked again; start a new one.
6. After reading the answer, check the findings that matter yourself (open the file at the line). Both vendors report false positives.

## Known limits

- agy's `response.md` does not exist until the round finishes; until then progress is visible only in the pane (or `events.jsonl`).
- agy uses many tokens: the same review cost about 2.5 times as many as codex in testing.
- codex's stderr may carry ERROR lines from the user's own codex setup (for example MCP servers that fail to authenticate). Those lines alone do not fail a round: the status comes from the exit code and the event stream on stdout. Read `stderr.log` when a round fails.
- Talking to an interactive codex or agy pane is not supported: that would mean typing into it and scraping the screen, which is what xv exists to avoid.
- `--profile` works with codex only.
