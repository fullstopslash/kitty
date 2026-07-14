import os
import sys

from kitty.fast_data_types import get_os_window_size

_last_orientation: dict[int, str] = {}
_DEBUG = os.environ.get('KITTY_SMART_LAYOUT_DEBUG') == '1'


def _log(msg: str) -> None:
    if _DEBUG:
        print(f'[smart_layout] {msg}', file=sys.stderr, flush=True)


def _desired_layout(os_window_id: int) -> str | None:
    metrics = get_os_window_size(os_window_id)
    if not metrics:
        return None
    width = metrics.get('width', 0)
    height = metrics.get('height', 0)
    if width <= 0 or height <= 0:
        return None
    return 'fat' if height >= width else 'tall'


def on_resize(boss, window, data):
    os_window_id = window.os_window_id
    desired = _desired_layout(os_window_id)
    if desired is None:
        return
    if _last_orientation.get(os_window_id) == desired:
        return
    tm = boss.os_window_map.get(os_window_id)
    if tm is None:
        return
    _log(f'os_window={os_window_id} -> {desired}')
    _last_orientation[os_window_id] = desired
    for tab in tm:
        if tab.current_layout.name in ('tall', 'fat') and tab.current_layout.name != desired:
            tab.goto_layout(desired)
