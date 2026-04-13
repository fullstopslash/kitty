"""SSH-aware tab/window spawning kitten.

Checks if the current window is running SSH/Mosh, and if so, opens a
new tab/window with SSH to the same host. If the remote session is
running tmux (detected via window title), attaches to tmux automatically.
Otherwise, opens a new tab/window in the current directory.

Usage in kitty.conf:
    map kitty_mod+t kitten ~/.config/kitty/ssh_smart_tab.py tab
    map kitty_mod+enter kitten ~/.config/kitty/ssh_smart_tab.py window
"""

import os
import re
from kittens.tui.handler import result_handler

# SSH options that take an argument
SSH_OPTIONS_WITH_ARG = frozenset([
    '-b', '-c', '-D', '-E', '-F', '-I', '-J', '-L', '-l', '-m',
    '-O', '-o', '-p', '-Q', '-R', '-S', '-W', '-w', '-i', '-B',
])


def detect_ssh_target(cmdline: list[str]) -> str | None:
    """Detect SSH/Mosh target from command line, preserving user@host."""
    if not cmdline:
        return None

    prog = os.path.basename(cmdline[0])
    if prog not in ('ssh', 'slogin', 'mosh', 'mosh-client'):
        return None

    args = cmdline[1:]
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == '--':
            return None
        if tok.startswith('-'):
            if tok in SSH_OPTIONS_WITH_ARG and i + 1 < len(args):
                i += 2
            else:
                i += 1
            continue
        target = tok.strip()
        if ':' in target and '@' in target:
            target = target.rsplit(':', 1)[0]
        elif ':' in target and target.count(':') == 1:
            target = target.split(':', 1)[0]
        return target
    return None


def detect_remote_tmux(title: str) -> bool:
    """Check if window title suggests tmux is running on the remote."""
    # Common tmux title patterns: "[0] 0:bash", "tmux: ...", or
    # title set by tmux's set-titles containing session/window info
    if not title:
        return False
    # tmux default titles often have [session] window:pane format
    if re.match(r'^\[.*\]\s+\d+:', title):
        return True
    if 'tmux' in title.lower():
        return True
    return False


def main(args: list[str]) -> str:
    pass


@result_handler(no_ui=True)
def handle_result(args: list[str], answer: str, target_window_id: int, boss) -> None:
    spawn_type = 'tab'
    i = 1
    while i < len(args):
        if args[i] in ('tab', 'window'):
            spawn_type = args[i]
        i += 1

    window = boss.active_window
    if window is None:
        return

    # Detect SSH target from foreground processes
    ssh_target = None
    try:
        for proc in window.child.foreground_processes:
            cmdline = proc.get('cmdline', [])
            target = detect_ssh_target(cmdline)
            if target:
                ssh_target = target
                break
    except Exception:
        pass

    if ssh_target:
        # Check if remote is running tmux based on window title
        if detect_remote_tmux(window.title or ''):
            ssh_cmd = ('ssh', '-t', ssh_target, 'tmux', 'new-session', '-A')
        else:
            ssh_cmd = ('ssh', ssh_target)

        if spawn_type == 'window':
            launch_args = ('launch',) + ssh_cmd
        else:
            launch_args = ('launch', '--type=tab') + ssh_cmd
    else:
        if spawn_type == 'window':
            launch_args = ('launch', '--cwd=current')
        else:
            launch_args = ('launch', '--type=tab', '--cwd=current')

    boss.call_remote_control(None, launch_args)
