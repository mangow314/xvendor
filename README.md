# xvendor

**Ask codex or agy for a read-only second opinion from inside Claude Code,
watch them work in a tmux pane, and keep every answer in a job directory.**

A review from a second model catches things the first one misses, but
getting one is clumsy. You copy context into another CLI by hand, or let
Claude drive an interactive codex session by typing into it and scraping the
screen. And the other model runs with whatever permissions its CLI has, so a
"review" can quietly edit files. `xv` makes that one command: it runs each
vendor's official headless CLI (`codex exec`, `agy -p`) read-only, shows
what it does live, and writes the request, the event stream and the final
answer to disk, so Claude (or you) can read them and ask follow-ups in the
same conversation.

![xv flow: Claude Code writes a brief and runs xv start codex; xv runs codex read-only, or agy in a read-only bubblewrap mount; a tmux pane shows each action live; the answer lands in a job directory and Claude reads response.md](docs/assets/xv-flow.svg)

![xv start codex in tmux: a pane opens beside the shell, lists each command codex runs, then prints the answer and done | 17s](docs/assets/xv.gif)

*`xv start codex --file brief.md` prints a job id and opens a pane beside
the caller. The pane lists each command codex runs, then prints the answer
once the round is done.*

The repo holds two things:

| Part | What it is |
| --- | --- |
| `xv` | A Python command-line tool. Works on its own, from any shell |
| `xvendor` plugin | A Claude Code skill that tells Claude when and how to use `xv`: writing a self-contained brief, running two vendors in parallel without leaking one's answer to the other, following up, checking findings |

> 繁體中文版：[README.zh-TW.md](README.zh-TW.md)

## Requirements

- Linux. Tested on Arch Linux; codex jobs may also work on macOS but are
  untested there.
- Python 3.12 or later, and [uv](https://docs.astral.sh/uv/) to install.
- At least one vendor CLI, installed and logged in:
  [codex](https://github.com/openai/codex) (`codex exec`) or agy (Google's
  Antigravity CLI, `agy -p`).
- [bubblewrap](https://github.com/containers/bubblewrap) (`bwrap`) on Linux,
  for both vendors. Without it, `xv start` refuses to run. Each vendor must
  also have been run once interactively so its state directory exists;
  otherwise the round fails (the tmux pane shows what is missing).
- Optional: tmux. Inside tmux each job gets a live pane; outside tmux it runs
  in the background and you read the result with `xv show`.
- For the plugin: Claude Code.

## Install

```bash
uv tool install git+https://github.com/mangow314/xvendor
```

Then, for Claude Code:

```bash
claude plugin marketplace add mangow314/xvendor
claude plugin install xvendor@xvendor
```

If you use Claude Code's Bash sandbox, `xv` has to run outside it: it writes
the job directory, opens tmux panes, and the vendor CLIs need the network.
Add it to `sandbox.excludedCommands` in your settings:

```json
{ "sandbox": { "excludedCommands": ["xv *"] } }
```

The skill tells Claude to call `xv` as a command of its own; a chained
command (`cd … && xv …`) does not match the entry and stays in the sandbox.

## Usage

With the plugin installed, ask Claude in your own words: "have codex review
this diff", "get a second opinion from agy on the plan". From a shell:

```bash
xv start codex --file brief.md     # prints the job id, opens a pane in tmux
xv start agy -m "Read src/ and list the public functions without tests"
xv wait <job>                      # block until the round ends; rc 0 = done
xv show <job>                      # print the answer (response.md)
xv ask <job> -m "Explain finding 2 in more detail"   # follow-up, same conversation
xv stop <job>                      # cut a round short; the session is kept
xv list                            # all jobs with vendor, round, status, seconds
xv close <job>                     # close the job's pane
xv watch <job>                     # follow a running round from any terminal

# codex profiles (files in ~/.codex, or $CODEX_HOME)
xv start codex --profile xv-review --file brief.md                 # review: web search off
xv start codex --profile xv-survey --cwd /path/to/clean/dir -m "…" # survey: cached web search

# write mode: the vendor may write inside one directory only
xv start codex --write /path/to/worktree --file brief.md
```

**Profiles.** `--profile NAME` (codex only) loads `NAME.config.toml` from
`$CODEX_HOME` (default `~/.codex`) on top of your base codex config. Two are
meant for `xv`; they are not shipped in this repo, so create them yourself:

`xv-review.config.toml`, for reviews: no web search, GitHub connector off.

```toml
web_search = "disabled"

[plugins."github@openai-curated"]
enabled = false

[shell_environment_policy]
ignore_default_excludes = false
exclude = ["*PASSWORD*", "*PASSW*", "*CRED*"]
```

`xv-survey.config.toml`, for research: cached web search. The pages it reads
can carry injected instructions, so give it a `--cwd` with nothing secret in it.

```toml
web_search = "cached"

[plugins."github@openai-curated"]
enabled = false

[shell_environment_policy]
ignore_default_excludes = false
exclude = ["*PASSWORD*", "*PASSW*", "*CRED*"]
```

**Findings as JSON.** For a review, ask the brief for a machine-readable
list so Claude can sort and check each item before acting on it:

```json
[{"severity": "high|medium|low", "file": "src/x.py", "line": 42,
  "symptom": "what is wrong", "trigger": "input or state that hits it", "fix_hint": "one line"}]
```

Open each finding's file at its line before you believe it: `response.md` is
the other model's output, data rather than instructions.

**Write mode.** `--write DIR` lets the vendor write inside `DIR` only
(`--cwd` defaults to `DIR`); everything else stays read-only. `DIR` must be
a linked worktree (`git worktree add <dir> -b <branch>`): a plain directory,
a repository root, and `--separate-git-dir` or submodule layouts are refused,
and so is a `DIR` under a path `xv` hides or under the vendor's state
directory. The vendor can run `git status` and `git diff`, but not `git add`
or `git commit`. Do not touch `DIR` yourself while a round is running. When
`xv wait` returns, review with `git -C DIR diff`, then commit and merge
yourself. Only one job at a time may write a given `DIR`; a second
`xv start` is refused. `ask` keeps the write directory set by `start`.
Once a round is over and you have committed or merged, run
`git worktree remove <dir>` (and `git branch -d` if needed) so unused
worktrees do not pile up as orphans.

`<job>` can be the full id or its last 4 characters. Each round has a
timeout (`--timeout` on `start`, default 1200 seconds; `ask` keeps the
job's timeout unless given a new one). Jobs live in
`~/.local/share/xvendor/jobs/<job-id>/` (set `XV_DATA` to an absolute path
of letters, digits, `/`, `.`, `_` and `-` to move them), one
folder per round with `request.md`, `events.jsonl`, `response.md` and
`stderr.log`.

## Safety

Every job is read-only unless you pass `--write DIR`, and this is enforced by
`xv` rather than left to the brief. On Linux both vendors run inside the same
bubblewrap (`bwrap`) layout:

- **Read-only root.** The whole filesystem is mounted read-only. The only
  writable places are the vendor's own state directory, a per-round temp
  directory (deleted afterwards, exported as `TMPDIR`), and, in write mode,
  `DIR` and the gitdir its `.git` file points to (`DIR` must be a linked
  worktree). But the `DIR/.git` pointer file and the gitdir's `commondir`,
  `gitdir` and `config.worktree` stay read-only, so the vendor cannot point
  the worktree at a git dir of its own and have your next git command run
  its config.
- **`/run` and X11 hidden.** `/run`, `/tmp/.X11-unix` and the default tmux
  socket directory (`$TMUX_TMPDIR` or `/tmp`, `tmux-<uid>`) are covered by
  empty in-memory folders. A read-only mount does not stop `connect()` to a
  path-based unix socket, and those directories hold the sockets that run
  commands outside the sandbox: `docker.sock` (anyone in the `docker` group
  is effectively root), the system and user D-Bus, `systemd --user`, the X11
  display, your tmux server. Only `/etc/resolv.conf`'s target is put back
  when it is a symlink into `/run`, so DNS still works.
- **Credentials blanked out** (directories become empty folders, files become
  empty files) under your home directory: `~/.ssh`, `~/.aws`, `~/.gnupg`,
  `~/.config/gh`, `~/.kube`, `~/.git-credentials`, `~/.npmrc`, `~/.pgpass`,
  `~/.netrc`, `~/.claude/.credentials.json`, `~/.codex/auth.json`,
  `~/.gemini/antigravity-cli/antigravity-oauth-token`,
  `~/.gemini/antigravity-cli/conversations`, `~/.gemini/oauth_creds.json`,
  `~/.gemini/google_accounts.json`, `~/.mozilla`, `~/.config/google-chrome`,
  `~/.config/chromium`, `~/.config/BraveSoftware`, `~/.config/Bitwarden CLI`,
  `~/.config/gcloud`, `~/.docker/config.json`, `~/.password-store`,
  `~/.claude.json`, `~/.claude/projects`, `~/.codex/sessions`,
  `~/.codex/history.jsonl`, plus per-project `.env` files:
  `~/projects/*/.env`, `~/projects/*/.env.*`, `~/projects/*/*/.env` and
  `~/projects/*/.envrc`. (The source of truth is `SECRET_PATHS` and
  `SECRET_GLOBS` in `src/xvendor/isolation.py`.) Entries inside the vendor's
  own state directory are skipped for that vendor, so it keeps its own login.
- **State directory writable, config locked.** The vendor must be able to
  write its own state, so that directory is writable, but the entries a later
  unsandboxed run would load stay read-only:
  - agy, `~/.gemini/antigravity-cli`: `settings.json`, `bin`, `builtin`.
  - codex, `$CODEX_HOME` (default `~/.codex`): `config.toml`, `AGENTS.md`,
    `hooks`, `rules`, `skills`, `plugins`, `agents`, and every
    `*.config.toml` profile file. `config.toml` and `hooks` must exist,
    otherwise the round is refused rather than run unprotected.
- **Environment scrubbed.** Variables whose name matches `key`, `token`,
  `secret`, `passw`, `cred` or `auth` (case-insensitive) are not passed to the
  vendor, and neither is the `GIT_CONFIG_*` family. These are kept regardless:
  `XV_DATA`, `XV_PANE`, `CODEX_HOME`, `PATH`, `HOME`, `TMPDIR`, `TERM`,
  `LANG`, `TMUX`, `TMUX_PANE` and `LC_*`. Inside bwrap `TMUX` and `TMUX_PANE`
  are unset. Vendors log in through files, not environment variables.
- **codex's own sandbox** (`-s read-only`, or `workspace-write` in write mode)
  stays on as a second layer. Follow-up rounds pass the current mode's
  sandbox again (`read-only` for a read-only job, `workspace-write` for a
  write job) via `-c sandbox_mode=...`, because a resumed codex session does
  not keep the first round's flag.
- **agy's own `--sandbox`** does not stop its file-writing tools, so `xv`
  does not rely on it.
- **Refuse, never degrade.** `xv start agy` refuses when `bwrap` is missing
  or the platform is not Linux. On Linux codex also needs `bwrap`.
  **On macOS codex runs without `xv` isolation**: only codex's own sandbox
  applies, and `xv` prints a note to say so.

What this does not cover:

- Network access is not blocked. Anything a vendor can read can leave with
  its conversation, so treat every readable file as shared with that vendor.
- Memory poisoning. The vendor's state directory is writable, and that
  includes its memory (agy `brain/`, codex `memories/`). A prompt injected
  through a file or web page can be saved there and steer your next
  interactive, unsandboxed codex or agy session. `xv` does not stop this.
- A tmux server on a custom socket (`tmux -S <path>` outside the default
  directory) is not hidden from the vendor.
- The vendor can read every file that is not on the list above, including
  other jobs in `~/.local/share/xvendor/`. The skill tells Claude to ask each
  vendor not to read that folder, but that is a request, not a wall.
- Write mode trusts the vendor inside `DIR`: review `git diff` before you
  commit, and do not put secrets in `DIR`.

## Adding a vendor

1. Implement the `Vendor` Protocol from `src/xvendor/vendors/base.py`, three
   methods: `build(round, session, prompt, profile, mode)` returns the
   command line (`mode` is `"read"` or `"write"`; pick the vendor's sandbox
   flag from it), `parse(line)` turns one event-stream line into an `Event`
   or `None`, and `result(lines)` returns `(session, response, ok)`.
2. Register it in `vendor_for` in `src/xvendor/cli.py` (and the `choices` of
   `xv start`).
3. Declare its isolation in `src/xvendor/isolation.py`: the state directory
   and which entries inside it stay read-only. Without that, `check()` refuses
   to run the vendor.

## Cost and privacy

- Each round is a normal `codex exec` or `agy -p` run on your own account
  and is billed or counted like any other use. agy used about 2.5 times as
  many tokens as codex for the same review in testing.
- Your brief, and every file the vendor chooses to read, go to that vendor
  (OpenAI for codex, Google for agy).
- `xv` itself sends nothing anywhere and keeps no data except the job
  directories on your disk.

## Uninstall

```bash
claude plugin uninstall xvendor@xvendor
claude plugin marketplace remove xvendor
uv tool uninstall xvendor
rm -rf ~/.local/share/xvendor    # job history, if you no longer want it
```

## History

xvendor replaced [agent-bridge](https://github.com/mangow314/agent-bridge)
in my own setup. agent-bridge typed tasks into interactive codex, Claude and
agy panes and read their screens; xvendor runs the vendors' official headless
CLIs instead, and makes read-only an enforced property of every job rather
than a request in the brief. If you want Claude workers, or workers that
spawn workers of their own, agent-bridge's README compares the alternatives
(built-in subagents, agent teams, cross-session messaging).

## Feedback

A personal project I use every day. Bug reports, "this confused me", and
ideas are all welcome in
[GitHub Issues](https://github.com/mangow314/xvendor/issues). Most useful:
your OS, the vendor CLI's version, the failing command, and the job's
`stderr.log`.

## Development

```bash
git clone https://github.com/mangow314/xvendor
cd xvendor
uv sync
uv run pytest
```

The tests use fake `codex` and `agy` scripts (`tests/fakes/`) that replay
recorded event streams. Tests that need tmux or bubblewrap are skipped when
those cannot start (for example in a sandbox that blocks unix sockets). How the GIF and
the diagram were made: [docs/demo/](docs/demo/).

Related: [xv-band](https://github.com/mangow314/mango-mods/tree/master/xv-band)
in mango-mods shows running xv jobs in a row above the Claude Code prompt.

## License

[MIT](LICENSE)
