"""Go to tab N if it exists, otherwise create tabs until N exists.

Usage in kitty.conf:
    map ctrl+shift+1 kitten goto_or_create_tab.py 1
    map ctrl+shift+2 kitten goto_or_create_tab.py 2
    ...
"""

from kittens.tui.handler import result_handler


def main(args: list[str]) -> str:
    pass


@result_handler(no_ui=True)
def handle_result(args: list[str], answer: str, target_window_id: int, boss) -> None:
    target_idx = int(args[1]) if len(args) > 1 else 1
    tm = boss.active_tab_manager
    if tm is None:
        return

    num_tabs = len(tm.tabs)
    if target_idx <= num_tabs:
        tab = tm.tabs[target_idx - 1]
        tm.set_active_tab(tab)
    else:
        boss.call_remote_control(None, ('launch', '--type=tab', '--cwd=current'))
