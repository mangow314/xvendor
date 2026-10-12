#!/bin/bash
# Record the tmux -L shot session to <name>.mp4 for <secs> seconds, in the current directory:
# vhs opens a 1130x720 terminal (120x34 cells), attaches to the session, and leaves when done.
# Drive the session with tmux -L shot send-keys meanwhile; cutting and GIF encoding are in README.md.
set -euo pipefail
name=$1
secs=$2
cat > "$name.tape" <<EOF
Output $name.mp4
Set Shell bash
Set FontFamily "Sarasa Term TC, Symbols Nerd Font"
Set FontSize 16
Set Width 1130
Set Height 720
Set Padding 12
Set Framerate 30
Set Theme GruvboxDarkHard
Hide
Type "tmux -L shot attach -t s"
Enter
Sleep 2s
Show
Sleep ${secs}s
EOF
timeout $((secs + 90)) vhs "$name.tape" >/dev/null 2>&1
rm -f "$name.tape"
ls -la "$name.mp4"
