
# ANSI color utilities using One Dark Pro theme.
def _rgb(r: int, g: int, b: int, bold: bool = True) -> str:
    """Generate ANSI 24-bit color escape sequence."""
    prefix = "\033[1;" if bold else "\033["
    return f"{prefix}38;2;{r};{g};{b}m"

# One Dark Pro theme colors

# Core palette
YELLOW = _rgb(229, 192, 123)
GREEN = _rgb(152, 195, 121)
RED = _rgb(224, 108, 117)
BLUE = _rgb(97, 175, 239)
PURPLE = _rgb(198, 120, 221)
CYAN = _rgb(86, 182, 194)
ORANGE = _rgb(209, 154, 102)

# Extended palette
FG = _rgb(171, 178, 191)  # Default foreground
COMMENT = _rgb(92, 99, 112)  # Comments / dim text
GUTTER = _rgb(76, 82, 99)  # Line numbers / subtle elements
WHITE = _rgb(255, 255, 255)
BLACK = _rgb(40, 44, 52)  # Background

# Semantic aliases
KEYWORD = PURPLE
STRING = GREEN
FUNCTION = BLUE
VARIABLE = RED
NUMBER = ORANGE
TYPE = YELLOW
OPERATOR = CYAN

# Reset
RESET = "\033[0m"
