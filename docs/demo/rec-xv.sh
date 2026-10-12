#!/bin/bash
# Record the raw video for xv.gif: in a throwaway repo, start a tmux -L shot session with a plain shell, run
# `xv start codex --file brief.md` from this checkout, and record the pane that opens until the round is done.
# Output (next to this file): xv-raw.mp4, events.log (seconds of each step; video 0 s is about the brief line),
# final.txt (the last screen). Cutting and GIF encoding: README.md, "GIF".
# Needs a logged-in codex, tmux, vhs and the network: run it outside Claude Code's Bash sandbox.
set -euo pipefail
# The first argument replaces brief.md, the request sent to codex (keep lines under 70 columns: the pane
# narrows the shell). Ask for English: codex otherwise follows its own instructions files.
brief=${1:-$'Review greet.py for bugs. Answer in English, with at most\ntwo findings, each as: [severity] file:line - problem'}
here=$(cd "$(dirname "$0")" && pwd)
M=$(cd "$here/../.." && pwd)
tmp=$(mktemp -d)
repo=$tmp/greet-demo
cd "$here"

if tmux -L shot has-session -t s 2>/dev/null; then
  echo "tmux -L shot already has a session s; deal with it first" >&2; exit 1
fi
[[ -x $M/.venv/bin/xv ]] || { echo "no $M/.venv/bin/xv; run uv sync in $M first" >&2; exit 1; }

rm -f xv-raw.mp4 xv-raw.tape events.log final.txt
mkdir -p "$repo"
cat > "$repo/greet.py" <<'EOF'
def average(xs):
    return sum(xs) / len(xs)


def greet(name):
    print("hello, " + name)


if __name__ == "__main__":
    greet("world")
    print(average([]))
EOF
git -C "$repo" init -q
git -C "$repo" add greet.py
git -C "$repo" -c user.name=demo -c user.email=demo@example.com commit -qm init
printf '%s\n' "$brief" > "$repo/brief.md"

# xv from this checkout, jobs in the throwaway dir, a short prompt
tmux -L shot -f /dev/null new-session -d -s s -x 120 -y 34 -c "$repo" \
  "env -u XV_DATA PATH=$M/.venv/bin:\$PATH XV_DATA=$tmp/xvdata PS1='\$ ' bash --norc --noprofile"
trap 'tmux -L shot kill-server 2>/dev/null || true' EXIT
tmux -L shot set -g status off
tmux -L shot set -g window-size manual
tmux -L shot set -g focus-events on
tmux -L shot set -g pane-border-style fg=colour240
tmux -L shot set -g pane-active-border-style fg=colour240
tmux -L shot set -as terminal-features 'xterm-256color:RGB'
tmux -L shot set -g default-terminal tmux-256color
sleep 1
tmux -L shot send-keys -t s 'clear' Enter

screen() { tmux -L shot capture-pane -p -t "$1"; }
# wait until any pane shows a string; on timeout print the screens and stop
wait_for() { # $1=string $2=seconds
  for ((i = 0; i < $2 * 2; i++)); do
    for p in $(tmux -L shot list-panes -t s -F '#{pane_id}'); do
      [[ $(screen "$p") == *"$1"* ]] && return 0
    done
    sleep 0.5
  done
  echo "timed out waiting for \"$1\"" >&2
  for p in $(tmux -L shot list-panes -t s -F '#{pane_id}'); do screen "$p" >&2; done
  return 1
}

start=$(date +%s.%N)
mark() { printf '%6.1f %s\n' "$(echo "$(date +%s.%N) - $start" | bc)" "$1" | tee -a events.log; }

"$here/record.sh" xv-raw 90 &
rec=$!
sleep 4
mark brief
tmux -L shot send-keys -t s 'cat brief.md' Enter
sleep 2.5
mark start
tmux -L shot send-keys -t s 'xv start codex --file brief.md' Enter
wait_for 'round 1' 15 && mark pane
wait_for 'full text: xv show' 85 && mark done
wait "$rec" || true
for p in $(tmux -L shot list-panes -t s -F '#{pane_id}'); do screen "$p"; echo ---; done > final.txt
tmux -L shot kill-server
echo "done: $here/xv-raw.mp4"
command cat events.log
