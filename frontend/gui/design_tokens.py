"""Single source of truth for both workspace themes and icon geometry."""
from types import MappingProxyType

ICON_SIZE = 18
ICON_STROKE = 1.75
THEMES = {
    'light': MappingProxyType({
        'canvas': '#f8f9fc', 'surface': '#ffffff', 'surface-muted': '#f0f2f7', 'sidebar': '#f3f5f9',
        'text': '#192338', 'muted': '#64718a', 'subtle': '#7c879b', 'border': '#e1e6ef',
        'accent': '#4f5fe8', 'accent-hover': '#3d4ed0', 'accent-soft': '#e9edff', 'on-accent': '#ffffff',
        'danger': '#b83e47', 'danger-soft': '#fff0f1', 'success': '#18745c', 'success-soft': '#e8f5ef',
        'warning': '#8a5c11', 'warning-soft': '#fff5df', 'focus': '#a3afff',
        'shadow': '0 12px 40px rgb(25 35 56 / 6%)', 'overlay-shadow': '0 8px 28px rgb(25 35 56 / 12%)',
    }),
    'dark': MappingProxyType({
        'canvas': '#11151e', 'surface': '#181e2a', 'surface-muted': '#222b3b', 'sidebar': '#141a25',
        'text': '#edf1fa', 'muted': '#a3afc4', 'subtle': '#8b98b0', 'border': '#2c374b',
        'accent': '#9ba8ff', 'accent-hover': '#b7c0ff', 'accent-soft': '#283253', 'on-accent': '#141a2d',
        'danger': '#ff9fa7', 'danger-soft': '#3a252e', 'success': '#8ddcba', 'success-soft': '#1d352f',
        'warning': '#edc77c', 'warning-soft': '#3a3121', 'focus': '#9ba8ff',
        'shadow': '0 12px 40px rgb(0 0 0 / 14%)', 'overlay-shadow': '0 8px 28px rgb(0 0 0 / 32%)',
    }),
}


def tokens(theme='light'):
    return THEMES.get(theme, THEMES['light'])


def variables(theme='light'):
    colors = ';'.join(f'--color-{key}:{value}' for key, value in tokens(theme).items())
    return colors + f';--icon-size:{ICON_SIZE}px;--icon-stroke:{ICON_STROKE};color-scheme:{theme}'
