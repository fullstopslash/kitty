from encode_key import encode_key_mapping
from kittens.tui.handler import result_handler


def main():
    pass


@result_handler(no_ui=True)
def handle_result(args, result, target_window_id, boss):
    window = boss.window_id_map.get(target_window_id)

    cmd = window.child.foreground_cmdline[0]
    if cmd == 'tmux':
        keymap = args[2]
        encoded = encode_key_mapping(window, keymap)
        window.write_to_child(encoded)
    else:
        boss.active_tab.neighboring_window(args[1])
