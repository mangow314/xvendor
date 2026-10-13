# How the pictures were made

## GIF (xv.gif)

A real `xv start codex` run (codex-cli 0.162.0), recorded in a throwaway repo
holding a short `greet.py` with a division by zero. Requirements: `tmux`,
[vhs](https://github.com/charmbracelet/vhs), `ffmpeg`, `bc`, the fonts Sarasa Term
TC and Symbols Nerd Font, a logged-in `codex`, and `uv sync` run once in this
repo (the script uses `.venv/bin/xv` from this checkout).

1. Record: run [rec-xv.sh](rec-xv.sh) from the repo root, outside Claude
   Code's Bash sandbox (it needs the tmux socket, codex's login and the
   network). It makes the throwaway repo, writes `brief.md`, starts a plain
   shell in its own tmux server (`tmux -L shot`, 120x34, no status bar) with
   jobs going to a temp `XV_DATA`, starts [record.sh](record.sh) in the
   background, runs `cat brief.md` and `xv start codex --file brief.md`, and
   waits for `full text: xv show` in the pane. It writes `xv-raw.mp4` and
   `events.log` (seconds of each step; video 0 s is about the `brief` line)
   next to itself. The first argument replaces the brief; ask for English,
   or codex may follow its own instructions files and answer in another
   language.
2. Cut, crop and encode, from `docs/demo/`. The crop keeps the first 21 character rows (12 px
   padding + 20.3 px per row); the rest of the screen is empty:

   ```bash
   ffmpeg -i xv-raw.mp4 -filter_complex "[0:v]\
   trim=0:24.5,setpts=PTS-STARTPTS,crop=1130:440:0:0,fps=10,\
   scale=800:-1:flags=lanczos,split[x][y];[x]palettegen=max_colors=64:stats_mode=diff[p];\
   [y][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" ../assets/xv.gif
   ```

   The end time depends on how long codex takes; read it off `events.log`
   (`done`) and a few frames (`ffmpeg -ss <t> -i xv-raw.mp4 -frames:v 1 t.png`),
   and keep about 3 seconds after `done`.

## Concept animation (xv-flow.svg)

An SVG with CSS keyframes, a 14-second loop on a dark card so it reads on
both GitHub themes; `aria-label` describes it and `prefers-reduced-motion`
shows the last frame. To check a change, render still frames: copy the SVG,
drop the `animation-name` declarations, hide the classes whose fade-in comes
after the chosen second (`.p4 { opacity: 0 }` and so on), and run
`rsvg-convert still.svg -o still.png`.
