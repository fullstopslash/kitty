"""SSH-aware tab/window spawning kitten.

Checks if the current window is running SSH/Mosh, and if so, opens a
new tab/window with SSH to the same host. If the remote session is
running tmux (detected via window title), attaches to tmux automatically.
Otherwise, opens a new tab/window in the current directory.

For local tabs inside a shpool session, the fg-process cwd is the
shpool-attach binary (frozen at launch ~), so --cwd=current is wrong.
The inner shell publishes its cwd to $XDG_RUNTIME_DIR/session-cwd/<name>
via a zsh chpwd hook; this kitten reads it back.

When the current OS window already holds MAX_TABS tabs, `tab` spawns a
new OS window instead. Pass `force` to bypass the cap, or `max=N` to
change it (`max=0` disables).

Usage in kitty.conf:
    map kitty_mod+t kitten ~/.config/kitty/smart_tab.py tab
    map kitty_mod+alt+t kitten ~/.config/kitty/smart_tab.py tab force
    map kitty_mod+enter kitten ~/.config/kitty/smart_tab.py window
"""

import os
import re
from kittens.tui.handler import result_handler

MAX_TABS = 5

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


def detect_session_name(cmdline: list[str]) -> str | None:
    """If cmdline is a persistent-session attach, return the session name.

    Recognizes: `shpool attach NAME`.
    """
    if not cmdline:
        return None
    prog = os.path.basename(cmdline[0])
    if prog == 'shpool' and len(cmdline) >= 3 and cmdline[1] == 'attach':
        return cmdline[2]
    return None


def resolve_session_cwd(cmdline: list[str]) -> str | None:
    """Read the inner-shell cwd published by _session_cwd_publish (zshrc)."""
    name = detect_session_name(cmdline)
    if not name:
        return None
    if os.sep in name or name in ('.', '..'):
        return None
    runtime_dir = os.environ.get('XDG_RUNTIME_DIR') or '/tmp'
    path = os.path.join(runtime_dir, 'session-cwd', name)
    try:
        with open(path) as f:
            cwd = f.read(4096).strip()
    except OSError:
        return None
    return cwd if cwd and os.path.isdir(cwd) else None


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
    force = False
    max_tabs = MAX_TABS
    i = 1
    while i < len(args):
        if args[i] in ('tab', 'window'):
            spawn_type = args[i]
        elif args[i] == 'force':
            force = True
        elif args[i].startswith('max='):
            try:
                max_tabs = int(args[i][4:])
            except ValueError:
                pass
        i += 1

    window = boss.active_window
    if window is None:
        return

    # Overflow to a new OS window once the tab cap is reached
    if spawn_type == 'tab' and not force and max_tabs > 0:
        tm = boss.active_tab_manager
        if tm is not None and len(tm.tabs) >= max_tabs:
            spawn_type = 'os-window'

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

        launch_args = ('launch', f'--type={spawn_type}') + ssh_cmd
    else:
        # Resolve cwd. If fg is a persistent-session attach (shpool),
        # read the published cwd file; otherwise use kitty's tracked cwd.
        cwd_opt = '--cwd=current'
        try:
            for proc in window.child.foreground_processes:
                session_cwd = resolve_session_cwd(proc.get('cmdline', []))
                if session_cwd:
                    cwd_opt = f'--cwd={session_cwd}'
                    break
        except Exception:
            pass

        launch_args = ('launch', f'--type={spawn_type}', cwd_opt)

    try:
        boss.call_remote_control(None, launch_args)
    except Exception:
        # Degrade to a plain new tab so the keypress still does something.
        try:
            boss.call_remote_control(None, ('launch',))
        except Exception:
            pass
