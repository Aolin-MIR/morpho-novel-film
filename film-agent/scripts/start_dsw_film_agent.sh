#!/usr/bin/env bash
set -euo pipefail
umask 077

root=/mnt/AIRSSD/user_workspace/liaolin2/morpho-personal/film-agent
agent="$root/code/scripts/dsw_film_agent.py"
launcher="$root/code/scripts/dsw_h3_start.sh"
test "$(id -un)" = liaolin2
test "$(realpath -e "$root")" = "$root"
test -f "$agent"
test -f "$launcher"

# Those sessions belong to the previous single-worker layout.
for old in morpho-agent-video morpho-agent-video-alt; do
    if tmux has-session -t "=$old" 2>/dev/null; then
        printf 'Drain the legacy worker before starting the shared queue. Session %s\n' "$old" >&2
        exit 1
    fi
done

for lane in cpu research text picture; do
    session="morpho-agent-$lane"
    if ! tmux has-session -t "=$session" 2>/dev/null; then
        tmux new-session -d -s "$session" -c "$root" /usr/bin/python3 "$agent" work "$lane"
    fi
done

for gpu in 0 1; do
    port="$(bash "$launcher" "$gpu" --port)"
    session="morpho-h3-gpu$gpu"
    if curl --noproxy '*' -fsS -m 3 "http://127.0.0.1:$port/queue" >/dev/null 2>&1; then
        if ! /usr/bin/python3 - "$gpu" "$port" <<'PY'
import os
from pathlib import Path
import sys

gpu, port = sys.argv[1:]
for candidate in Path('/proc').glob('[0-9]*'):
    try:
        if candidate.stat().st_uid != os.getuid():
            continue
        args = (candidate / 'cmdline').read_bytes().split(b'\0')
        if len(args) < 2 or not args[1].endswith(b'/morpho-h3/ComfyUI/main.py'):
            continue
        chosen_port = args[args.index(b'--port') + 1] if b'--port' in args else b''
        if chosen_port == port.encode() and f'CUDA_VISIBLE_DEVICES={gpu}'.encode() in (
                candidate / 'environ').read_bytes().split(b'\0'):
            break
    except (OSError, IndexError):
        continue
else:
    sys.exit('The listener does not match the personal H3 service on this GPU.')
PY
        then
            exit 1
        fi
    elif ! tmux has-session -t "=$session" 2>/dev/null; then
        tmux new-session -d -s "$session" -c "$root" bash "$launcher" "$gpu"
    fi
    worker="morpho-agent-video-gpu$gpu"
    if ! tmux has-session -t "=$worker" 2>/dev/null; then
        tmux new-session -d -s "$worker" -c "$root" /usr/bin/python3 "$agent" work video --gpu "$gpu"
    fi
done
/usr/bin/python3 "$agent" gpu-status
