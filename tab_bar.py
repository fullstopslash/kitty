import os
import re
import sys
import fnmatch
from dataclasses import dataclass, field
from typing import Dict, Iterator
from kitty.fast_data_types import Screen, get_boss, get_options
from kitty.rgb import Color
from kitty.tab_bar import (
    DrawData,
    ExtraData,
    TabBarData,
    as_rgb,
    draw_title,
)
from kitty.utils import color_as_int


def _log_warning(msg: str) -> None:
    """Log a warning message to stderr."""
    print(f"[tab_bar.py] Warning: {msg}", file=sys.stderr)


MAX_CONFIG_SIZE_BYTES = 1024 * 1024  # 1MB


def _read_config_file(path: str) -> list[str] | None:
    """Safely read a config file with size validation."""
    try:
        size = os.path.getsize(path)
        if size > MAX_CONFIG_SIZE_BYTES:
            _log_warning(
                f"Config file too large ({size} bytes), limit is {MAX_CONFIG_SIZE_BYTES}"
            )
            return None
        with open(path, "r", encoding="utf-8") as f:
            return f.readlines()
    except OSError as e:
        _log_warning(f"Failed to read config: {e}")
        return None


def _strip_yaml_value(val: str) -> str:
    """Strip inline comments and surrounding quotes from a YAML value."""
    val = val.strip()
    if " #" in val:
        val = val.split(" #", 1)[0].strip()
    if val and len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
        val = val[1:-1]
    return val


def _normalize_yaml_key(raw: str, lowercase: bool = True) -> str:
    """Normalize a YAML key by stripping whitespace and quotes."""
    key = raw.strip()
    if len(key) >= 2 and key[0] in ('"', "'") and key[-1] == key[0]:
        key = key[1:-1]
    return key.lower() if lowercase else key


def _normalize_color_key(key: str) -> str:
    """Normalize color key names (index-color -> ring-color)."""
    return "ring-color" if key == "index-color" else key


def _resolve_color_with_precedence(
    color_key: str,
    host_colors: dict[str, str] | None,
    app_colors: dict[str, str] | None,
    default: str,
) -> str:
    """Resolve color name with precedence: host > app > default."""
    return (
        (host_colors.get(color_key) if host_colors else None)
        or (app_colors.get(color_key) if app_colors else None)
        or default
    )


def _safe_compile_regex(pattern: str, flags: int = 0) -> re.Pattern | None:
    """Safely compile a regex pattern with basic ReDoS protection."""
    if len(pattern) > 500:
        _log_warning(f"Rejecting overly long regex pattern ({len(pattern)} chars)")
        return None
    try:
        return re.compile(pattern, flags)
    except re.error as e:
        _log_warning(f"Invalid regex pattern '{pattern}': {e}")
        return None


# Configuration for icons and ring symbols
#
# Example nerd-font-window-name.yml (global + per-app)
# ---
# config:
#   show-name: false
#   use-process-name: false
#   index-color-active: color99
#   index-color-inactive: color237
#   icon-color: color33
#   alert-color: color1
# icons:
#   nvim:
#     icon: "󰴓"
#     index-color: color99
#     icon-color: color33
#     alert-color: color1
#   zsh:
#     icon: "󱆃"
#     icon-color: "#a6e3a1"
#   ranger: "󰷏"
#
# Example host-icons.yml (per-host icon + optional colors)
# ---
# config:
#   prefer-host-icon: true
# hosts:
#   prod.example.com:
#     icon: "󰣇"
#     index-color: "#fab387"
#     icon-color: color33
#     alert-color: "#f38ba8"
#   "*.staging.example.com": "󱓞"
UNIFIED_ICON_CONFIG_DEFAULT = os.path.expanduser("~/.config/nerd-icons/nerd-icons.yml")
ICON_CONFIG_DEFAULT = os.path.expanduser("~/.config/nerd-icons/config.yml")
# Support both names for host config for backward-compat
HOST_ICON_CONFIG_CANDIDATES = [
    os.path.expanduser("~/.config/nerd-icons/hosts.yml"),
    os.path.expanduser("~/.config/nerd-icons/host-icons.yml"),
]
RING_SYMBOLS = "󰬺 󰬻 󰬼 󰬽 󰬾 󰬿 󰭀 󰭁 󰭂 󰿩".split()


@dataclass
class IconConfig:
    """Configuration loaded from nerd-icons.yml."""

    fallback_icon: str = "󰽙"
    use_process_name: bool = False
    show_name: bool = False
    prefer_host_icon: bool = True
    ring_color_active: str = "color99"
    ring_color_inactive: str = "color237"
    icon_color: str = "color33"
    alert_color: str = "color1"
    layout_hints_enabled: bool = False
    layout_glyphs: dict[str, str] = field(default_factory=dict)
    app_color_map: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass
class IconCache:
    """Runtime caches for icon/host lookups."""

    icon_map: dict[str, str | dict[str, str]] = field(default_factory=dict)
    title_patterns: dict[str, list[tuple[re.Pattern, str]]] = field(
        default_factory=dict
    )
    host_icon_exact: dict[str, str] = field(default_factory=dict)
    host_icon_patterns: list[tuple[str, str]] = field(default_factory=list)
    host_color_exact: dict[str, dict[str, str]] = field(default_factory=dict)
    host_color_patterns: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    icon_config_loaded: bool = False
    host_config_loaded: bool = False


# Module-level instances
_config = IconConfig()
_cache = IconCache()


def _parse_bool(val: str) -> bool:
    v = _normalize_yaml_key(val)
    if v in ("true", "yes", "1", "on"):
        return True
    if v in ("false", "no", "0", "off"):
        return False
    return False


def _iter_yaml_block(
    lines: list[str],
    block_labels: str | tuple[str, ...],
) -> Iterator[tuple[int, str, str | None, int]]:
    """Iterate over items in a YAML block.

    Args:
        lines: Raw file lines
        block_labels: Label(s) to match (e.g., "icons:" or ("layout-glyphs:", "layout-glyps:"))

    Yields:
        (line_index, key, inline_value_or_None, base_indent)

    If inline_value is None, the entry has nested content that the caller should parse.
    """
    if isinstance(block_labels, str):
        block_labels = (block_labels,)

    in_block = False
    block_indent: int | None = None

    for idx, raw in enumerate(lines):
        stripped = raw.strip()

        if not in_block:
            if any(stripped.startswith(label) for label in block_labels):
                in_block = True
            continue

        if not stripped or raw.lstrip().startswith("#"):
            continue

        current_indent = len(raw) - len(raw.lstrip())
        if block_indent is None:
            block_indent = current_indent

        if current_indent < block_indent:
            break

        if ":" not in raw:
            continue

        try:
            key_part, val_part = stripped.split(":", 1)
            key = _normalize_yaml_key(key_part)
            rest = val_part.strip()

            # Determine if this is an inline value or nested block
            if rest and not rest.startswith(("#", "|", ">", "{", "[")):
                inline_val = _strip_yaml_value(rest)
                yield (idx, key, inline_val, current_indent)
            else:
                yield (idx, key, None, current_indent)
        except Exception:
            continue


def _resolve_icon_config_path() -> str | None:
    """Resolve the icon config path with precedence:
    1) $KITTY_ICON_CONFIG if set
    2) unified nerd-icons.yml if it exists
    3) fallback to Kitty's local nerd-font YAML
    """
    env_path = os.environ.get("KITTY_ICON_CONFIG")
    if env_path and os.path.isfile(os.path.expanduser(env_path)):
        return os.path.expanduser(env_path)
    if os.path.isfile(UNIFIED_ICON_CONFIG_DEFAULT):
        return UNIFIED_ICON_CONFIG_DEFAULT
    if os.path.isfile(ICON_CONFIG_DEFAULT):
        return ICON_CONFIG_DEFAULT
    return None


def _load_icon_map() -> None:
    """Load a minimal icon map from the YAML file without external deps.

    Expected structure (subset):
    ---
    config:
      fallback-icon: "..."
    icons:
      nvim: "..."
      zsh: "..."
    """
    global _config, _cache
    # Reset caches on reload
    _cache.icon_map = {}
    _cache.title_patterns = {}
    _config.app_color_map = {}
    _config.layout_glyphs = {}
    cfg_path = _resolve_icon_config_path()
    if not cfg_path:
        return
    lines = _read_config_file(cfg_path)
    if lines is None:
        return

    # Parse top-level config: block only (avoid leaking per-app colors into globals)
    in_config = False
    config_indent = None
    for raw in lines:
        stripped = raw.strip()
        if not in_config:
            if stripped.startswith("config:"):
                in_config = True
            continue
        if not stripped or raw.lstrip().startswith("#"):
            continue
        current_indent = len(raw) - len(raw.lstrip())
        if config_indent is None:
            config_indent = current_indent
        if current_indent < config_indent:
            break
        if ":" not in raw:
            continue
        try:
            key, val = raw.strip().split(":", 1)
            key = _normalize_yaml_key(key)
            part = _strip_yaml_value(val)
            if key == "fallback-icon" and part:
                _config.fallback_icon = part
            elif key == "use-process-name":
                _config.use_process_name = _parse_bool(part)
            elif key == "show-name":
                _config.show_name = _parse_bool(part)
            elif key == "layout-hints":
                _config.layout_hints_enabled = _parse_bool(part)
            elif key in ("ring-color-active", "index-color-active") and part:
                _config.ring_color_active = part
            elif key in ("ring-color-inactive", "index-color-inactive") and part:
                _config.ring_color_inactive = part
            elif key in ("ring-color", "index-color") and part:
                _config.ring_color_active = part
                _config.ring_color_inactive = part
            elif key == "icon-color" and part:
                _config.icon_color = part
            elif key == "alert-color" and part:
                _config.alert_color = part
        except Exception:
            pass

    # Helper to parse a specific block label ("icons:" or "apps:")
    def _parse_app_block(block_label: str) -> None:
        nonlocal lines
        in_block = False
        indent_level_local = None
        for idx, raw in enumerate(lines):
            if not in_block:
                if raw.strip().startswith(block_label):
                    in_block = True
                continue
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            current_indent = len(raw) - len(raw.lstrip())
            if indent_level_local is None:
                indent_level_local = current_indent
            if current_indent < indent_level_local:
                break
            if ":" not in raw:
                continue
            stripped = raw.strip()
            try:
                key_part, val_part = stripped.split(":", 1)
                app_key = _normalize_yaml_key(key_part)
                rest = val_part.strip()
                if not app_key:
                    continue
                # Inline scalar icon value
                if (
                    rest
                    and not rest.startswith("#")
                    and not rest.startswith("|")
                    and not rest.startswith(">")
                    and not rest.startswith("{")
                    and not rest.startswith("[")
                ):
                    val = _strip_yaml_value(rest)
                    if val:
                        _cache.icon_map[app_key] = val
                    continue
            except Exception as e:
                _log_warning(f"Error parsing {block_label} at line {idx + 1}: {e}")
                continue
            # Otherwise nested mapping; walk subsequent lines for fields
            base_indent = current_indent
            j = idx + 1
            nested_icon = None
            colors: Dict[str, str] = {}
            title_patterns: Dict[str, str] = {}
            while j < len(lines):
                lj = lines[j]
                if not lj.strip() or lj.lstrip().startswith("#"):
                    j += 1
                    continue
                indent_j = len(lj) - len(lj.lstrip())
                if indent_j <= base_indent:
                    break
                if ":" in lj:
                    try:
                        kp, vp = lj.strip().split(":", 1)
                        sk = _normalize_yaml_key(kp)
                        sv = _strip_yaml_value(vp)
                        if sk == "icon":
                            nested_icon = sv
                        elif sk == "title":
                            # Handle title: sub-block with pattern mappings
                            # e.g. title: ".*github.*": "icon"
                            title_base_indent = indent_j
                            k = j + 1
                            while k < len(lines):
                                lk = lines[k]
                                if not lk.strip() or lk.lstrip().startswith("#"):
                                    k += 1
                                    continue
                                indent_k = len(lk) - len(lk.lstrip())
                                if indent_k <= title_base_indent:
                                    break
                                if ":" in lk:
                                    try:
                                        tkp, tvp = lk.strip().split(":", 1)
                                        pattern = _normalize_yaml_key(
                                            tkp, lowercase=False
                                        )
                                        tval = _strip_yaml_value(tvp)
                                        if pattern and tval:
                                            title_patterns[pattern] = tval
                                    except Exception:
                                        pass
                                k += 1
                            j = k - 1  # Skip the title block we just parsed
                        elif sk in (
                            "ring-color",
                            "index-color",
                            "icon-color",
                            "alert-color",
                        ):
                            if sk == "index-color":
                                colors["ring-color"] = sv
                            else:
                                colors[sk] = sv
                    except Exception:
                        pass
                j += 1
            if nested_icon:
                _cache.icon_map[app_key] = nested_icon
            # Store title patterns under a special key for later lookup
            if title_patterns:
                _cache.icon_map[f"{app_key}:title"] = title_patterns
                # Pre-compile regex patterns for performance (use safe compilation)
                compiled_patterns = []
                for pattern, icon in title_patterns.items():
                    compiled = _safe_compile_regex(pattern, re.IGNORECASE)
                    if compiled is not None:
                        compiled_patterns.append((compiled, icon))
                if compiled_patterns:
                    _cache.title_patterns[app_key] = compiled_patterns
            if colors:
                _config.app_color_map[app_key] = colors

    # Parse blocks independently if present (in priority order: sessions, icons, apps, title_icons)
    _parse_app_block("sessions:")
    _parse_app_block("icons:")
    _parse_app_block("apps:")
    _parse_app_block("title_icons:")

    # app-colors block (optional, backward-compatible)
    in_app_colors = False
    indent_level = None
    for idx, raw in enumerate(lines):
        if not in_app_colors:
            if raw.strip().startswith("app-colors:"):
                in_app_colors = True
            continue
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        current_indent = len(raw) - len(raw.lstrip())
        if indent_level is None:
            indent_level = current_indent
        if current_indent < indent_level:
            break
        if ":" not in raw:
            continue
        # parse app key and nested mapping
        try:
            key_part, _ = raw.strip().split(":", 1)
            app_key = _normalize_yaml_key(key_part)
        except Exception:
            continue
        base_indent = current_indent
        j = idx + 1
        colors: Dict[str, str] = {}
        while j < len(lines):
            lj = lines[j]
            if not lj.strip() or lj.lstrip().startswith("#"):
                j += 1
                continue
            indent_j = len(lj) - len(lj.lstrip())
            if indent_j <= base_indent:
                break
            if ":" in lj:
                try:
                    kp, vp = lj.strip().split(":", 1)
                    sk = _normalize_yaml_key(kp)
                    sv = _strip_yaml_value(vp)
                    if sk in ("ring-color", "index-color", "icon-color", "alert-color"):
                        if sk == "index-color":
                            colors["ring-color"] = sv
                        else:
                            colors[sk] = sv
                except Exception:
                    pass
            j += 1
        if colors:
            # Merge with existing if present, otherwise set
            if app_key in _config.app_color_map:
                _config.app_color_map[app_key].update(colors)
            else:
                _config.app_color_map[app_key] = colors

    # layout-glyphs block (optional) - using generic iterator
    for _idx, k, v, _indent in _iter_yaml_block(
        lines, ("layout-glyphs:", "layout-glyps:")
    ):
        if k and v:
            _config.layout_glyphs[k] = v

    _cache.icon_config_loaded = True


def _load_host_icon_map() -> None:
    """Load host icon mappings from HOST_ICON_CONFIG_PATH.

    Structure:
    ---
    config:
      prefer-host-icon: true
    hosts:
      myhost: "icon"
      "*.prod.example.com": "icon"
    """
    global _config, _cache
    lines = None
    # Always prefer the separate host-icons.yml for security
    for p in HOST_ICON_CONFIG_CANDIDATES:
        if os.path.isfile(p):
            lines = _read_config_file(p)
            if lines is not None:
                break
    if not lines:
        return
    # reset host caches before (re)loading (don't reset icon cache - that's handled by _load_icon_map)
    _cache.host_icon_exact = {}
    _cache.host_icon_patterns = []
    _cache.host_color_exact = {}
    _cache.host_color_patterns = []

    # config
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "prefer-host-icon:" in line:
            try:
                part = line.split(":", 1)[1].strip()
                part = part.split(" #", 1)[0].strip()
                _config.prefer_host_icon = _parse_bool(part)
            except Exception:
                pass

    # hosts block
    in_hosts = False
    indent_level = None
    for idx, raw in enumerate(lines):
        if not in_hosts:
            if raw.strip().startswith("hosts:"):
                in_hosts = True
            continue
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        # support spaces or tabs
        current_indent = len(raw) - len(raw.lstrip())
        if indent_level is None:
            indent_level = current_indent
        if current_indent < indent_level:
            break
        # Support two formats under hosts:
        # 1) key: "icon"
        # 2) key:
        #      icon: "..."
        #      ring-color: "..."
        #      icon-color: "..."
        #      alert-color: "..."
        if ":" not in raw:
            continue
        stripped = raw.strip()
        try:
            key_part, val_part = stripped.split(":", 1)
            key = _normalize_yaml_key(key_part, lowercase=False)
            rest = val_part.strip()
            key_lc = key.lower()
            # Inline scalar icon
            if (
                rest
                and not rest.startswith("#")
                and not rest.startswith("|")
                and not rest.startswith(">")
                and not rest.startswith("{")
                and not rest.startswith("[")
            ):
                val = _strip_yaml_value(rest)
                if any(ch in key_lc for ch in ["*", "?", "["]):
                    _cache.host_icon_patterns.append((key_lc, val))
                else:
                    _cache.host_icon_exact[key_lc] = val
                continue
            # Otherwise, it's a mapping block; read subsequent indented lines
            base_indent = current_indent
            colors: Dict[str, str] = {}
            icon_val: str | None = None
            # Peek following lines while indent > base_indent
            # We need the original list iterator with index
        except Exception:
            continue
        j = idx + 1
        while j < len(lines):
            line_j = lines[j]
            if not line_j.strip() or line_j.lstrip().startswith("#"):
                j += 1
                continue
            indent_j = len(line_j) - len(line_j.lstrip())
            if indent_j <= base_indent:
                break
            # parse k: v
            if ":" in line_j:
                try:
                    kp, vp = line_j.strip().split(":", 1)
                    sk = _normalize_yaml_key(kp)
                    sv = _strip_yaml_value(vp)
                    if sk == "icon":
                        icon_val = sv
                    elif sk in (
                        "ring-color",
                        "index-color",
                        "icon-color",
                        "alert-color",
                    ):
                        if sk == "index-color":
                            colors["ring-color"] = sv
                        else:
                            colors[sk] = sv
                except Exception:
                    pass
            j += 1
        # assign collected
        if icon_val:
            if any(ch in key_lc for ch in ["*", "?", "["]):
                _cache.host_icon_patterns.append((key_lc, icon_val))
            else:
                _cache.host_icon_exact[key_lc] = icon_val
        if colors:
            if any(ch in key_lc for ch in ["*", "?", "["]):
                _cache.host_color_patterns.append((key_lc, colors))
            else:
                _cache.host_color_exact[key_lc] = colors

    _cache.host_config_loaded = True


def _detect_ssh_host(window) -> str | None:
    """Best-effort parse of SSH/Mosh target host from foreground cmdline."""
    try:
        argv = window.child.foreground_cmdline
    except Exception:
        return None

    if not argv:
        return None
    prog = os.path.basename(argv[0])
    args = list(argv[1:])

    def extract_host_token(tok: str) -> str | None:
        s = tok.strip()
        if not s:
            return None
        # strip user@
        if "@" in s:
            s = s.split("@", 1)[1]
        # strip brackets and :port
        if s.startswith("[") and "]" in s:
            s = s[1 : s.index("]")]
        if ":" in s and s.count(":") == 1:
            # likely host:port, keep host part
            s = s.split(":", 1)[0]
        # basic validations: ipv4, ipv6, domain/hostname
        ipv4 = re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", s)
        ipv6 = ":" in s and re.match(r"^[0-9A-Fa-f:]+$", s)
        domain = re.match(r"^[A-Za-z0-9_.-]+$", s)
        if ipv4 or ipv6 or domain:
            return s
        return None

    def take_first_non_option(_args: list[str]) -> str | None:
        i = 0
        # options that consume next arg
        consumes_next = {
            "-b",
            "-c",
            "-D",
            "-E",
            "-F",
            "-I",
            "-J",
            "-L",
            "-l",
            "-m",
            "-O",
            "-o",
            "-p",
            "-Q",
            "-R",
            "-S",
            "-W",
            "-w",
            "-i",
            "-B",
        }
        while i < len(_args):
            tok = _args[i]
            if tok == "--":
                return None
            if tok.startswith("-"):
                if tok in consumes_next and i + 1 < len(_args):
                    i += 2
                    continue
                i += 1
                continue
            return tok
        return None

    host = None
    if prog in ("ssh", "slogin"):
        host = take_first_non_option(args)
    elif prog in ("mosh", "mosh-client"):
        host = take_first_non_option(args)
    # Do not attempt generic fallback scanning to avoid false positives when not using SSH-like tools
    if not host:
        return None
    # strip user@, brackets, and :port
    if "@" in host:
        host = host.split("@", 1)[1]
    host = host.strip()
    if host.startswith("[") and "]" in host:
        host = host[1 : host.index("]")]
    if ":" in host:
        host = host.split(":", 1)[0]
    return host or None


def _get_host_info(window) -> tuple[str | None, Dict[str, str] | None]:
    """Get both host icon and colors in one pass to avoid duplicate host detection."""
    if not _cache.host_config_loaded:
        _load_host_icon_map()

    host = _detect_ssh_host(window)
    if not host:
        return None, None
    host_lc = host.lower()

    # exact match first
    icon = _cache.host_icon_exact.get(host_lc)
    colors = _cache.host_color_exact.get(host_lc)
    if icon or colors:
        return icon, colors

    # wildcard patterns in declared order
    for pattern, icn in _cache.host_icon_patterns:
        try:
            if fnmatch.fnmatch(host_lc, pattern):
                # Find matching color pattern
                col = None
                for col_pattern, col_val in _cache.host_color_patterns:
                    if fnmatch.fnmatch(host_lc, col_pattern):
                        col = col_val
                        break
                return icn, col
        except Exception:
            continue

    return None, None


def _get_host_icon(window) -> str | None:
    icon, _ = _get_host_info(window)
    return icon


def _get_host_colors(window) -> Dict[str, str] | None:
    _, colors = _get_host_info(window)
    return colors


def _get_icon_for_tab(index: int) -> tuple[str, str | None]:
    if not _cache.icon_config_loaded:
        _load_icon_map()
    icon = _config.fallback_icon
    matched_app: str | None = None
    try:
        tm = get_boss().active_tab_manager
        if tm is None:
            return icon, matched_app
        # draw_tab receives 1-based index
        py_tab = tm.tabs[index - 1]
        window = py_tab.active_window
        # Prefer host icon when configured
        if _config.prefer_host_icon and window is not None:
            host_icon = _get_host_icon(window)
            if host_icon:
                return host_icon, None
        candidates = []
        window_title = ""
        if window is not None:
            # Use window title variants (preferred)
            try:
                title = window.title or ""
                window_title = title
                if title:
                    candidates.append(title)
                    # split title into tokens by non-word separators
                    for tok in re.split(r"[^A-Za-z0-9._+-]+", title):
                        if tok:
                            candidates.append(tok)
            except Exception:
                pass
            # Optionally include foreground process name if enabled
            if _config.use_process_name:
                try:
                    cmdline = window.child.foreground_cmdline
                    if cmdline:
                        base = os.path.basename(cmdline[0])
                        candidates.append(base)
                except Exception:
                    pass
        # Check for title pattern matches first (against full window title)
        if window_title:
            # Use pre-compiled patterns from cache for performance
            for app_key, compiled_list in _cache.title_patterns.items():
                for pattern, icon in compiled_list:
                    if pattern.search(window_title):
                        return icon, app_key
        # Then check for exact matches in candidates
        for cand in candidates:
            k = cand.lower()
            # Check for exact match
            if k in _cache.icon_map:
                val = _cache.icon_map[k]
                # Skip dict values (title patterns already checked above)
                if not isinstance(val, dict):
                    return val, k
    except Exception:
        pass
    return icon, matched_app


opts = get_options()


def _has_bell_or_activity(tab: TabBarData) -> bool:
    try:
        if getattr(tab, "has_activity_since_last_focus", False):
            return True
    except Exception:
        pass
    for attr in ("is_urgent", "bell_is_urgent", "is_bell"):
        try:
            if getattr(tab, attr, False):
                return True
        except Exception:
            continue
    return False


## Removed dedicated _has_bell helper; using _has_bell_or_activity exclusively


def _resolve_color(name: str, default_attr: str, default_fg: int) -> int:
    # Hex value
    try:
        if name.startswith("#"):
            h = name.lstrip("#")
            if len(h) in (6, 8):
                r = int(h[0:2], 16)
                g = int(h[2:4], 16)
                b = int(h[4:6], 16)
                return as_rgb(color_as_int(Color(r, g, b)))
    except Exception:
        pass
    # Kitty option name
    try:
        return as_rgb(color_as_int(getattr(opts, name)))
    except Exception:
        pass
    # Fallback to provided default_attr
    try:
        return as_rgb(color_as_int(getattr(opts, default_attr)))
    except Exception:
        return default_fg


def _draw_prefix(screen: Screen, tab: TabBarData, index: int) -> None:
    ring = RING_SYMBOLS[(index - 1) % len(RING_SYMBOLS)]
    app_icon, app_key = _get_icon_for_tab(index)
    prev_fg = screen.cursor.fg
    # Choose ring color (alert > active/inactive)
    # Host-specific overrides (apply only when host icon would be used)
    host_colors = None
    try:
        tm = get_boss().active_tab_manager
        window = tm.tabs[index - 1].active_window if tm else None
        if window is not None and _config.prefer_host_icon:
            host_icon_for_window, host_colors = _get_host_info(window)
            if not host_icon_for_window:
                host_colors = None  # Only use colors if we have a host icon
    except Exception:
        host_colors = None

    app_colors = _config.app_color_map.get(app_key) if app_key else None

    # safety override removed; use explicit alert-color via config instead

    if _has_bell_or_activity(tab):
        alert_name = _resolve_color_with_precedence(
            "alert-color", host_colors, app_colors, _config.alert_color
        )
        ring_color = _resolve_color(alert_name, "color1", prev_fg)
    else:
        is_active = index == get_active_tab_index()
        ring_name = (
            # Focused tab color takes precedence over SSH/app overrides
            (_config.ring_color_active if is_active else None)
            or _resolve_color_with_precedence(
                "ring-color", host_colors, app_colors, _config.ring_color_inactive
            )
        )
        ring_color = _resolve_color(ring_name, "foreground", prev_fg)
    icon_name = _resolve_color_with_precedence(
        "icon-color", host_colors, app_colors, _config.icon_color
    )
    icon_color = _resolve_color(icon_name, "foreground", prev_fg)

    # Draw ring
    screen.cursor.fg = ring_color
    screen.draw(ring)
    screen.draw("")
    # Draw icon
    screen.cursor.fg = icon_color
    screen.draw(app_icon)
    screen.draw(" ")
    screen.cursor.fg = prev_fg


def get_active_tab_index() -> int:
    return get_boss().active_tab_manager.active_tab_idx + 1


def draw_tab(
    draw_data: DrawData,
    screen: Screen,
    tab: TabBarData,
    before: int,
    max_tab_length: int,
    index: int,
    is_last: bool,
    extra_data: ExtraData,
) -> int:
    # Get colors directly from kitty options to respect config
    is_active_tab = index == get_active_tab_index()

    if is_active_tab:
        # Use _resolve_color to properly get colors from config
        # This handles both option names and direct hex values
        active_bg = _resolve_color(
            "active_tab_background", "background", as_rgb(color_as_int(Color(1, 1, 11)))
        )
        active_fg = _resolve_color(
            "active_tab_foreground", "foreground", screen.cursor.fg
        )
    else:
        # Use config colors for inactive tab
        active_bg = _resolve_color(
            "inactive_tab_background",
            "background",
            as_rgb(color_as_int(Color(24, 24, 37))),
        )
        active_fg = _resolve_color(
            "inactive_tab_foreground", "foreground", screen.cursor.fg
        )

    default_bg = as_rgb(int(draw_data.default_bg))

    if extra_data.next_tab:
        next_tab_bg = as_rgb(draw_data.tab_bg(extra_data.next_tab))
    else:
        next_tab_bg = default_bg
    if extra_data.prev_tab:
        prev_tab_bg = as_rgb(draw_data.tab_bg(extra_data.prev_tab))
    else:
        prev_tab_bg = default_bg

    if is_active_tab:
        screen.cursor.fg = active_bg
        screen.cursor.bg = prev_tab_bg
        screen.draw("")
        screen.cursor.fg = active_fg
        screen.cursor.bg = active_bg
        screen.draw(" ")
        _draw_prefix(screen, tab, index)
        if _config.show_name:
            draw_title(draw_data, screen, tab, index, max_tab_length)
        # ----------
        extra = screen.cursor.x + 1 - before - max_tab_length
        if extra > 0 and extra + 1 < screen.cursor.x:
            screen.cursor.x -= extra
            screen.draw("…")
        # ----------
        screen.draw(" ")
        screen.cursor.fg = active_bg
        screen.cursor.bg = next_tab_bg
        screen.draw("")
    elif index < get_active_tab_index():
        if index == 1:
            screen.cursor.fg = active_bg
            screen.cursor.bg = default_bg
            screen.draw("")
            screen.cursor.fg = active_fg
            screen.cursor.bg = active_bg
        else:
            screen.cursor.fg = default_bg
            screen.draw("")
        screen.draw(" ")
        _draw_prefix(screen, tab, index)
        if _config.show_name:
            draw_title(draw_data, screen, tab, index, max_tab_length)
        # ----------
        extra = screen.cursor.x + 1 - before - max_tab_length
        # print(screen.cursor.x,before,max_tab_length,"->",extra)
        if extra > 0 and extra + 1 < screen.cursor.x:
            screen.cursor.x -= extra
            screen.draw("…")
        # ----------
        screen.draw(" ")
    elif index > get_active_tab_index():
        screen.draw(" ")
        _draw_prefix(screen, tab, index)
        if _config.show_name:
            draw_title(draw_data, screen, tab, index, max_tab_length)
        # ----------
        extra = screen.cursor.x + 2 - before - max_tab_length
        if extra > 0 and extra + 1 < screen.cursor.x:
            screen.cursor.x -= extra
            screen.draw("…")
        # ----------
        screen.draw(" ")
        if is_last:
            screen.cursor.fg = active_bg
            screen.cursor.bg = default_bg
            screen.draw("")
        else:
            screen.cursor.fg = default_bg
            screen.draw("")

    end = screen.cursor.x
    # if end < screen.columns:
    #     screen.draw(' ')
    return end


## pulse helper removed
