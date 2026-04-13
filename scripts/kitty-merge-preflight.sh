#!/usr/bin/env bash
# Kitty window merge — orchestrator.
# Pre-checks, launches picker, executes merges with no focus disruption.
set -euo pipefail

mode="${1:-}"

target_pid=$(hyprctl clients -j 2>/dev/null | jq -r '
    [.[] | select(.class == "kitty")] |
    sort_by(.focusHistoryID) |
    .[0].pid // empty
') && [[ -n "$target_pid" ]] || { notify-send "kitty-merge" "No kitty window found" 2>/dev/null; exit 0; }

SOCK="unix:@mykitty-${target_pid}"
os_count=$(kitty @ ls --to "$SOCK" 2>/dev/null | jq 'length' 2>/dev/null || echo 0)
[[ "$os_count" -lt 2 ]] && { notify-send "kitty-merge" "Only one kitty window open" 2>/dev/null; exit 0; }

plan=$(mktemp)
trap 'rm -f "$plan"' EXIT

kitty --class picker -e \
    ~/.config/kitty/scripts/kitty-merge-windows.sh \
    --plan "$plan" --sock "$SOCK" $mode

[[ -s "$plan" ]] || { hyprctl dispatch focuswindow "pid:$target_pid" 2>/dev/null; exit 0; }

# Merge while picker still covers kitty — detach-window --stay-in-tab
# preserves active tab focus, so no flicker when kitty regains visibility.
ok=0
while read -r wid; do
    kitty @ detach-window --to "$SOCK" \
        --match "id:$wid" --target-tab new --stay-in-tab 2>/dev/null \
        && ((ok++)) || true
done < "$plan"

hyprctl dispatch focuswindow "pid:$target_pid" 2>/dev/null || true
true
