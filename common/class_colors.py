import json

# Single source of truth for GPCR class colours, keyed by short class code (derived from the
# protein_family slug, never the display name). Change a colour here and it applies everywhere:
# Python payloads import GPCR_CLASS_COLORS, and home/base.html exposes it to JS as
# window.GPCR_CLASS_COLORS via the protwis.context_processors.gpcr_class_colors processor.
GPCR_CLASS_COLORS = {
    "A": "#1f78b4",
    "B1": "#33a02c",
    "B2": "#6A3D9A",
    "C": "#d62728",
    "D1": "#8C564B",
    "F": "#FF7F0E",
    "T2": "#F7B6D2",
    "O1": "#C51B7D",
    "O2": "#52C2CC",
    "V1": "#B8860B",
    "U": "#9e9e9e",
}

# Class-level protein_family slug -> short class code. Exposed to JS as window.GPCR_CLASS_CODE_BY_SLUG.
GPCR_CLASS_CODE_BY_SLUG = {
    "001": "A",
    "002": "B1",
    "003": "B2",
    "004": "C",
    "005": "D1",
    "006": "F",
    "007": "O1",
    "008": "O2",
    "009": "T2",
    "010": "V1",
    "011": "U",
}


def class_color_for_slug(slug, default=None):
    """Colour for any protein_family slug (only its first 3 characters, the class level, are used)."""
    return GPCR_CLASS_COLORS.get(GPCR_CLASS_CODE_BY_SLUG.get(str(slug or "")[:3]), default)


GPCR_CLASS_COLORS_JSON = json.dumps(GPCR_CLASS_COLORS)
GPCR_CLASS_CODE_BY_SLUG_JSON = json.dumps(GPCR_CLASS_CODE_BY_SLUG)
