from kittens.tui.handler import result_handler


def main(args):
    pass


@result_handler(no_ui=True)
def handle_result(args, result, target_window_id, boss):
    window = boss.window_id_map.get(target_window_id)
    if window is None:
        return
    tab = boss.active_tab
    if tab is None:
        return
    g = window.geometry
    width = g.right - g.left
    height = g.bottom - g.top
    layout = 'fat' if height >= width else 'tall'
    if tab.current_layout.name != layout:
        tab.goto_layout(layout)
    boss.launch('--cwd=current')
