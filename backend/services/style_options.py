"""The subtitle style options the renderer accepts, as one payload for a client.

Every value here is read from the request schema and the renderer's own font table
rather than from a hand-kept list, so a mode, font or bound added in one place is
offered by the style page without a second edit. A hand-kept list would drift, and the
drift shows up as a style the UI promises and the API rejects.
"""

from __future__ import annotations

from typing import Any, Final, cast

from schemas.clip import (
    ClipStyleOptions,
    FontChoice,
    NumericRange,
    StyleChoice,
    SubtitleCharRule,
    SubtitleConfig,
    SubtitleWordRule,
)
from workers.clips.subtitles import FONT_CHOICES

_SUBTITLE_SCHEMA: Final[dict[str, Any]] = SubtitleConfig.model_json_schema()
_PROPERTIES: Final[dict[str, dict[str, Any]]] = _SUBTITLE_SCHEMA["properties"]
_WORD_RULE_PROPERTIES: Final[dict[str, dict[str, Any]]] = SubtitleWordRule.model_json_schema()[
    "properties"
]
_CHAR_RULE_PROPERTIES: Final[dict[str, dict[str, Any]]] = SubtitleCharRule.model_json_schema()[
    "properties"
]

# Sliders for bounds that are not whole numbers: a finer step is usable, and the value
# still stays inside the range the API enforces.
_FLOAT_STEPS: Final[dict[str, float]] = {
    "outline": 0.5,
    "shadow": 0.5,
    "box_opacity": 0.05,
    "silence_gap_threshold": 0.1,
    "rotation": 1.0,
    "area_scale": 0.05,
}

# Spoken the way a picker should read them rather than as their enum spelling.
_VALUE_LABELS: Final[dict[str, dict[Any, str]]] = {
    "border_style": {1: "Outline & shadow", 3: "Background box"},
    "display_mode": {"full": "Full Sentence"},
}


def _label(value: Any) -> str:
    """``single_word`` becomes ``Single Word`` so a picker never shows an enum spelling."""
    return " ".join(part.capitalize() for part in str(value).replace("_", " ").split())


def _unwrap(schema: dict[str, Any]) -> dict[str, Any]:
    """The real shape of a field the schema declares as optional.

    Pydantic writes an optional field as ``anyOf: [{shape}, {null}]``, which would hide
    the enum and the bounds one level down from where this module reads them.
    """
    for option in schema.get("anyOf", []):
        if option.get("type") != "null":
            return cast(dict[str, Any], option)
    return schema


def _choices(field: str, properties: dict[str, dict[str, Any]] = _PROPERTIES) -> list[StyleChoice]:
    labels = _VALUE_LABELS.get(field, {})
    values = _unwrap(properties[field]).get("enum", [])
    return [StyleChoice(value=value, label=labels.get(value, _label(value))) for value in values]


def _numeric_range(schema: dict[str, Any], field: str) -> NumericRange:
    step = _FLOAT_STEPS.get(field)
    if step is None:
        step = 1 if schema["type"] == "integer" else 0.1
    return NumericRange(
        minimum=float(schema["minimum"]),
        maximum=float(schema["maximum"]),
        step=step,
    )


def _range_of(field: str) -> NumericRange:
    return _numeric_range(_PROPERTIES[field], field)


def _fonts() -> list[FontChoice]:
    return [
        FontChoice(value=requested, label=requested, renders_as=face)
        for requested, face in FONT_CHOICES
    ]


def clip_style_options() -> ClipStyleOptions:
    """Every style the API will accept, ready to be sent to a client as one document."""
    return ClipStyleOptions(
        defaults=SubtitleConfig(),
        display_modes=_choices("display_mode"),
        animations=_choices("animation"),
        positions=_choices("position"),
        border_styles=_choices("border_style"),
        fonts=_fonts(),
        font_size=_range_of("font_size"),
        outline=_range_of("outline"),
        shadow=_range_of("shadow"),
        box_opacity=_range_of("box_opacity"),
        silence_gap_threshold=_range_of("silence_gap_threshold"),
        rotation=_range_of("rotation"),
        area_scale=_range_of("area_scale"),
        word_rule_font_size=_numeric_range(
            _unwrap(_WORD_RULE_PROPERTIES["font_size"]), "font_size"
        ),
        word_rule_animations=_choices("animation", _WORD_RULE_PROPERTIES),
        max_word_rules=int(_PROPERTIES["custom_word_rules"]["maxItems"]),
        max_char_rules=int(_PROPERTIES["custom_char_rules"]["maxItems"]),
        max_rule_positions=int(_WORD_RULE_PROPERTIES["positions"]["maxItems"]),
    )
