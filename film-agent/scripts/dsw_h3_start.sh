#!/usr/bin/env bash
set -euo pipefail
umask 077

parent=/mnt/AIRSSD/user_workspace/liaolin2
root="$parent/morpho-h3"
agent="$parent/morpho-personal/film-agent"
gpu="${1:?Pass GPU 0 or 1}"
case "$gpu" in
    0) port=28188; data="$root"; temp="$root/tmp" ;;
    1) port=28191; data="$agent/h3/gpu-1"; temp="$data/tmp" ;;
    *) printf 'Only GPU 0 or 1 is available to this service.\n' >&2; exit 2 ;;
esac
[[ "$(id -un)" == liaolin2 && "$(realpath -e "$root")" == "$root" ]]
[[ "$(realpath -e "$agent")" == "$agent" ]]
[[ "$(realpath -m "$data")" == "$data" ]]
if [[ "${2:-}" == --port ]]; then
    printf '%s\n' "$port"
    exit 0
fi
mkdir -p "$data"/{input,output,user,cache} "$temp" "$agent/h3/logs"
exec >>"$agent/h3/logs/gpu-$gpu.log" 2>&1
exec 9>"$agent/h3/gpu-$gpu.lock"
flock -n 9

cd "$root"
waiting=0
while :; do
    memory="$(nvidia-smi --id="$gpu" --query-gpu=memory.used --format=csv,noheader,nounits)"
    [[ "$memory" =~ ^[0-9]+$ ]]
    if ((memory < 256)); then
        break
    fi
    if ((waiting == 0)); then
        printf '%s GPU %s held by an existing workload. Waiting for release.\n' "$(date -Is)" "$gpu"
        waiting=1
    fi
    sleep 15
done

export CUDA_VISIBLE_DEVICES="$gpu"
export XDG_CACHE_HOME="$data/cache" HF_HOME="$data/cache/huggingface" TMPDIR="$temp"
printf '%s Starting H3 video on GPU %s and port %s.\n' "$(date -Is)" "$gpu" "$port"
exec "$root/venv-cu130/bin/python" "$root/ComfyUI/main.py" \
    --base-directory "$root" --temp-directory "$temp" \
    --input-directory "$data/input" --output-directory "$data/output" --user-directory "$data/user" \
    --listen 127.0.0.1 --port "$port" --disable-all-custom-nodes --disable-auto-launch
