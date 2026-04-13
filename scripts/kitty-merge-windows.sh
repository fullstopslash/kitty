#!/usr/bin/env bash
# Kitty window/tab merge — fzf picker that writes window IDs to a plan file.
# Called by kitty-merge-preflight.sh which handles pre-checks and execution.
#
# Usage: kitty-merge-windows.sh --plan FILE --sock SOCK [--tabs]
set -euo pipefail
source ~/.config/hypr/scripts/picker-lib.sh || exit 1
focus_guard

MODE="windows" PLAN_FILE="" SOCK=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tabs) MODE="tabs" ;;
        --plan) PLAN_FILE="$2"; shift ;;
        --sock) SOCK="$2"; shift ;;
    esac
    shift
done
[[ -z "$PLAN_FILE" || -z "$SOCK" ]] && exit 1

KITTY_JSON=$(mktemp)
trap 'rm -f "$KITTY_JSON"; [ -n "${_focus_guard_pid:-}" ] && kill "$_focus_guard_pid" 2>/dev/null' EXIT
kitty @ ls --to "$SOCK" > "$KITTY_JSON" 2>/dev/null || exit 0

target_os_id=$(jq -r '(map(select(.is_active)) | .[0].id) // .[0].id' "$KITTY_JSON")
t_title=$(jq -r --argjson id "$target_os_id" '
    .[] | select(.id == $id) |
    (first(.tabs[] | select(.is_active)) // .tabs[0]).title // "target"
' "$KITTY_JSON")

if [[ "$MODE" == "windows" ]]; then
    entries=$(jq -r --argjson id "$target_os_id" '
        .[] | select(.id != $id) |
        "\(.id)|\(.tabs | length) tab(s)|\(first(.tabs[] | select(.is_active)) // .tabs[0] | .title // "untitled")"
    ' "$KITTY_JSON")
    with_nth='2..' prompt="merge windows> "
else
    entries=$(jq -r --argjson id "$target_os_id" '
        .[] | select(.id != $id) | .tabs[] |
        "\(.windows[0].id)|\(.windows[0].cwd // "~")|\(.title // "untitled")"
    ' "$KITTY_JSON")
    with_nth='3..' prompt="merge tabs> "
fi
[[ -z "$entries" ]] && exit 0

selected=$(echo "$entries" | fzf \
    --delimiter='|' --with-nth="$with_nth" \
    --header="Merge into: ${t_title:0:60}" \
    --prompt="$prompt" \
    --reverse --no-info --border \
    --multi) || exit 0

if [[ "$MODE" == "windows" ]]; then
    while IFS='|' read -r os_id _; do
        jq -r --arg id "$os_id" '
            .[] | select(.id == ($id | tonumber)) | .tabs[].windows[].id
        ' "$KITTY_JSON"
    done <<< "$selected"
else
    cut -d'|' -f1 <<< "$selected"
fi > "$PLAN_FILE"
