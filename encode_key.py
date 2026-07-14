# Shared helper for the tmux-aware kittens (neighboring_window, relative_resize,
# split_window). Each of them forwards a keypress to the child when the
# foreground process is tmux, and acts on kitty otherwise; this is the encoding
# half, kept in one place rather than copied into all three.
#
# Based on MIT licensed code at
# https://github.com/chancez/dotfiles/blob/master/kitty/.config/kitty/relative_resize.py
from kitty.key_encoding import KeyEvent, parse_shortcut


def encode_key_mapping(window, key_mapping):
    """Encode a kitty shortcut string into bytes the child terminal accepts."""
    mods, key = parse_shortcut(key_mapping)
    event = KeyEvent(
        mods=mods,
        key=key,
        shift=bool(mods & 1),
        alt=bool(mods & 2),
        ctrl=bool(mods & 4),
        super=bool(mods & 8),
        hyper=bool(mods & 16),
        meta=bool(mods & 32),
    ).as_window_system_event()

    return window.encoded_key(event)
