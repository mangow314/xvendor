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
- For agy: [bubblewrap](https://github.com/containers/bubblewrap) (`bwrap`),
  and agy run once interactively so its state directory exists. Without
  `bwrap`, `xv start agy` refuses to run; without the state directory, the
  round fails (the tmux pane shows what is missing).
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
```

`<job>` can be the full id or its last 4 characters. Each round has a
timeout (`--timeout` on `start`, default 1200 seconds; `ask` keeps the
job's timeout unless given a new one). Jobs live in
`~/.local/share/xvendor/jobs/<job-id>/` (set `XV_DATA` to an absolute path
of letters, digits, `/`, `.`, `_` and `-` to move them), one
folder per round with `request.md`, `events.jsonl`, `response.md` and
`stderr.log`.

## Safety

Every job is read-only, enforced by `xv` rather than left to the brief:

- **codex** runs with `-s read-only`, codex's own sandbox. Follow-up rounds
  pass `-c sandbox_mode="read-only"` again, because a resumed codex session
  does not keep the first round's flag.
- **agy** runs inside bubblewrap with the whole filesystem mounted
  read-only. On the host, only agy's own state directory
  (`~/.gemini/antigravity-cli`, minus its settings, launcher and built-in
  skills, which stay read-only) is writable; agy also gets a per-round temp
  directory, deleted afterwards. `/run/user/<uid>` and the default tmux
  socket directory (`$TMUX_TMPDIR` or `/tmp`, `tmux-<uid>`) are covered by
  empty in-memory folders, so agy cannot reach `systemd --user` or your
  default tmux server to run a command outside the sandbox. Credential files in
  your home directory (`~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`,
  `~/.kube`, `~/.git-credentials`, `~/.npmrc`, `~/.pgpass`, `~/.netrc`,
  `~/.claude/.credentials.json`, `~/.codex/auth.json`) are blanked out.
  agy's own `--sandbox` flag does not stop its file-writing tools, so xv does
  not rely on it. agy never runs without this: `xv start agy` refuses when
  `bwrap` is missing, and the round fails if agy's state directory is
  incomplete.

What this does not cover:

- Network access is not blocked. Anything a vendor can read can leave with
  its conversation, so treat every readable file as shared with that vendor.
- codex's read-only mode is codex's own sandbox; `xv` only makes sure the
  flag is always passed.
- A tmux server on a custom socket (`tmux -S <path>` outside the default
  directory) is not hidden from agy.
- agy can read every file that is not on the list above, including other
  jobs in `~/.local/share/xvendor/`. The skill tells Claude to ask each
  vendor not to read that folder, but that is a request, not a wall.

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
