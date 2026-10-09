"""Burned-in word-by-word subtitles for a clip, as an ASS subtitle document.

The pipeline the renderer uses:

1. one ``Dialogue`` event per word of the transcript, so exactly one word is on screen
   at a time (``display_mode`` ``single_word``);
2. the earlier words of the line are still drawn in the same event in the
   ``accumulate`` and ``karaoke`` modes, which is why the loop below walks the whole
   line instead of jumping to the active word;
3. the file is read by ffmpeg's ``ass`` filter, which renders the override tags.

The text is returned as a string rather than written to disk, so it can be tested
without touching the filesystem and so the caller decides where it lives.
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Final

from workers.clips.style import VIDEO_HEIGHT, VIDEO_WIDTH

# Anything that is not a word character or a space, so "Um," still matches "um".
PUNCTUATION = re.compile(r"[^\w\s]")

# The last word of a line is held on screen a little longer than it is spoken, so it
# does not flash for a single frame.
TAIL_HOLD_SECONDS = 0.5

# Fields every dialogue event carries after the style name; ASS requires them even
# when they are unused.
_EVENT_FIELDS = ",0,0,0,,"

# numpad-style anchor points, as ASS \an alignment numbers
ALIGNMENTS = {
    "top left": 7,
    "top center": 8,
    "top right": 9,
    "middle left": 4,
    "center": 5,
    "middle right": 6,
    "bottom left": 1,
    "below center": 2,
    "bottom right": 3,
}

DEFAULT_ALIGNMENT = ALIGNMENTS["below center"]

# Every font family a client may ask for, and the face bundled in ``assets`` that it is
# rendered with. Ordered as the UI should list them; ``FONT_FAMILY_MAP`` below is derived
# from this so a family offered to a client is always one the renderer can draw.
FONT_CHOICES: Final[list[tuple[str, str]]] = [
    ("DejaVu Sans", "DejaVu Sans"),
    ("Arial", "Arimo"),
    ("Inter", "Inter"),
    ("Montserrat", "Montserrat"),
    ("Poppins", "Poppins"),
    ("Roboto", "Roboto"),
    ("Oswald", "Oswald"),
    ("Georgia", "Lora"),
    ("Trebuchet MS", "Lato"),
    # The faces the style presets are built around: one heavy display face, one soft
    # rounded face, two monospaces for the typing looks and three serifs for the
    # cinematic looks.
    ("Anton", "Anton"),
    ("Fredoka", "Fredoka"),
    ("Space Mono", "Space Mono"),
    ("Courier Prime", "Courier Prime"),
    ("Playfair Display", "Playfair Display"),
    ("Cinzel", "Cinzel"),
    ("Cormorant Garamond", "Cormorant Garamond"),
]

FONT_FAMILY_MAP = {requested.casefold(): face for requested, face in FONT_CHOICES}

FONT_FILES = {
    "DejaVu Sans": "DejaVuSans.ttf",
    "Arimo": "Arimo.ttf",
    "Inter": "Inter.ttf",
    "Montserrat": "Montserrat.ttf",
    "Poppins": "Poppins.ttf",
    "Roboto": "Roboto.ttf",
    "Oswald": "Oswald.ttf",
    "Lora": "Lora.ttf",
    "Lato": "Lato.ttf",
    "Anton": "Anton-Regular.ttf",
    "Fredoka": "Fredoka.ttf",
    "Space Mono": "SpaceMono-Regular.ttf",
    "Courier Prime": "CourierPrime-Bold.ttf",
    "Playfair Display": "PlayfairDisplay.ttf",
    "Cinzel": "Cinzel.ttf",
    "Cormorant Garamond": "CormorantGaramond.ttf",
}

# The bright words a rainbow line cycles through, in order, so a playful preset gives
# every word its own colour without the client naming sixteen of them.
WORD_RAINBOW: Final[tuple[str, ...]] = (
    "#FF5A5F",
    "#FFC53D",
    "#3DDC97",
    "#4D9DE0",
    "#B15EFF",
    "#FF8A3D",
)


def resolve_font_name(font_name: str) -> str:
    """Resolve a UI/API family name to a font bundled with the clip renderer."""
    return FONT_FAMILY_MAP.get(str(font_name).strip().casefold(), "DejaVu Sans")


def seconds_to_ass_time(seconds: float) -> str:
    """Seconds into the ``H:MM:SS.cs`` timestamp ASS expects."""
    delta = datetime.timedelta(seconds=seconds)
    total = int(delta.total_seconds())
    centiseconds = delta.microseconds // 10_000
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}.{centiseconds:02d}"


def seconds_to_ffmpeg_time(seconds: float) -> str:
    """Seconds into the ``HH:MM:SS.mmm`` timestamp ffmpeg's ``-ss`` expects."""
    delta = datetime.timedelta(seconds=seconds)
    total = int(delta.total_seconds())
    milliseconds = delta.microseconds // 1_000
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}.{milliseconds:03d}"


def hex_to_ass_color(hex_color: str) -> str:
    """``#RRGGBB`` into ASS's ``&H00BBGGRR``, which is byte-reversed.

    Anything that is not six hex digits renders as white rather than as a broken
    override tag that would tint the rest of the line.
    """
    value = str(hex_color).lstrip("#")
    if len(value) != 6:
        return "&H00FFFFFF&"
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H00{blue}{green}{red}&"


def hex_to_ass_style_color(hex_color: str) -> str:
    """The same colour as the style section writes it: no trailing ``&``.

    The trailing ``&`` belongs to an override tag inside dialogue text. The ``[V4+ Styles]``
    section does not take it, and a reader that is handed one anyway drops the field and
    substitutes white — which is what happens when the file is rewritten while a clip is
    cut into pieces, silently throwing away the colour that was asked for.
    """
    return hex_to_ass_color(hex_color).removesuffix("&")


def _style_outline_color(config: dict[str, Any]) -> str:
    """The outline colour as the style section reads it, with the box opacity baked in.

    Border style 3 fills a box behind the words with the outline colour rather than
    stroking the letters, so the alpha byte of that colour is what decides how much of
    the box shows through. Border style 1 strokes the letters and stays fully opaque,
    which is also what every request that predates the opacity slider asks for.
    """
    value = str(config.get("outline_color", "#000000")).lstrip("#")
    if len(value) != 6:
        value = "000000"
    red, green, blue = value[0:2], value[2:4], value[4:6]
    if int(config.get("border_style", 1)) == 3:
        opacity = min(max(float(config.get("box_opacity", 1.0)), 0.0), 1.0)
        alpha = round((1.0 - opacity) * 255)
        return f"&H{alpha:02X}{blue}{green}{red}"
    return f"&H00{blue}{green}{red}"


def get_animation_tag(animation: str) -> str:
    """Word-level override tags for one entrance animation.

    Only vertical scale is animated. Scaling horizontally as well is what makes ASS
    text appear to slide sideways while it is meant to pop in place.

    ``none`` and ``typewriter`` have no tag of their own: the first draws the word as
    it is, and the second is timed letter by letter inside :func:`format_word`.
    ``blur_in`` is the slow focus-in the cinematic presets use, and ``bounce`` drops a
    word in small and tilted, springs it past its size, then settles it upright.
    """
    tags = {
        "none": "",
        "pop": r"{\fscy40\alpha&HFF&\t(0,150,\fscy100\alpha&H00&)}",
        "zoom_in": r"{\fscy200\alpha&HFF&\t(0,200,\fscy100\alpha&H00&)}",
        "zoom_out": r"{\fscy300\alpha&HFF&\t(0,250,\fscy100\alpha&H00&)}",
        "fade": r"{\alpha&HFF&\t(0,150,\alpha&H00&)}",
        "slide_right": r"{\clip(0,0,0,1080)\t(0,250,\clip(0,0,1920,1080))}",
        "slide_left": r"{\clip(1920,0,1920,1080)\t(0,250,\clip(0,0,1920,1080))}",
        "typewriter": "",
        "bounce": (
            r"{\fscy55\frz-10\alpha&HFF&"
            r"\t(0,220,\fscy115\frz6\alpha&H00&)"
            r"\t(220,340,\fscy100\frz0)}"
        ),
        "blur_in": r"{\blur7\alpha&HFF&\t(0,850,\blur0\alpha&H00&)}",
    }
    return tags.get(animation, "")


def animation_clips_line(animation: str) -> bool:
    """Whether an entrance animation hides the line with a clip region.

    libass applies ``\\clip`` to the entire dialogue line, not only to the text after
    the tag, so a clip-based entrance can never be confined to one word inside a line:
    the words around it would be clipped away with it. Those animations are drawn on
    a line of their own, where the rest of the sentence is present but invisible and
    so keeps the line laid out exactly the same.
    """
    return animation in ("slide_right", "slide_left")


def get_alignment_code(position: str) -> int:
    return ALIGNMENTS.get(position.lower(), DEFAULT_ALIGNMENT)


def _scaled_font_size(config: dict[str, Any], size: float) -> int:
    """Resize a font size by the subtitle-area scale from the drag widget.

    The area box and the text grow together, so the size the renderer draws is the one
    the widget displays, not the 8-300 slider value alone.
    """
    return max(1, round(size * float(config.get("area_scale", 1.0))))


def _position_prefix(config: dict[str, Any]) -> tuple[str, int]:
    """Override tags and alignment for one placement of every line.

    The style page pins the subtitle with its drag box, which sends both axes. A line
    without them (an old request, or the built-in fallback) keeps the registered anchor
    from ``position`` instead, so nothing that predates the widget changes behaviour.
    """
    x = config.get("position_x")
    y = config.get("position_y")
    if x is None or y is None:
        return "", get_alignment_code(config["position"])
    px = round(float(x) * VIDEO_WIDTH)
    py = round(float(y) * VIDEO_HEIGHT)
    tags = f"\\an5\\pos({px},{py})"
    rotation = round(float(config.get("rotation", 0)), 2)
    if rotation:
        # ``g`` so a whole number of degrees is written as ``15`` and not ``15.0``:
        # both read the same, but only one of them matches what the widget shows.
        tags += f"\\frz{rotation:g}"
    return f"{{{tags}}}", 5


def _is_filler(word: str, config: dict[str, Any]) -> bool:
    cleaned = PUNCTUATION.sub("", word).strip()
    if not cleaned:
        return True

    if cleaned.isdigit():
        return True

    if not config.get("remove_filler_words", False):
        return False
    fillers = {str(f).lower().strip() for f in config.get("filler_words_list", [])}
    return cleaned.lower() in fillers


def has_subtitles_in_range(
    transcript: list[dict[str, Any]],
    start: float,
    end: float,
    config: dict[str, Any],
) -> bool:
    """Whether the clip range contains a word the subtitle style will display."""
    for block in transcript:
        for word in block.get("words", []):
            text = str(word.get("word", "")).strip()
            if (
                text
                and not _is_filler(text, config)
                and float(word["start"]) < end
                and float(word["end"]) > start
            ):
                return True
    return False


def format_word(
    text: str,
    config: dict[str, Any],
    word_rule: dict[str, Any],
    is_active: bool,
    duration_ms: int,
    offset_ms: int = 0,
) -> str:
    """One word with its colour, size, font and per-character overrides applied.

    ``duration_ms`` and ``offset_ms`` place this word's typing inside its event: a word
    that starts a line types from ``offset_ms`` and lasts ``duration_ms``, so a line
    typed all at once has its words type one after the other instead of together.
    """
    color = hex_to_ass_color(word_rule.get("font_color", config["font_color"]))
    size = _scaled_font_size(config, word_rule.get("font_size", config["font_size"]))
    font = resolve_font_name(word_rule.get("font_name") or config["font_name"])
    bold = word_rule.get("bold", config["bold"])
    italic = word_rule.get("italic", config["italic"])

    if is_active and config["display_mode"] == "karaoke":
        base_color = hex_to_ass_color(config["highlight_color"])
    else:
        base_color = color

    # A whole-word override has to be opened before the letters and closed after
    # them, otherwise it bleeds into the next word in the same event.
    prefix = ""
    if "font_name" in word_rule:
        prefix += f"\\fn{font}"
    if "bold" in word_rule:
        prefix += f"\\b{-1 if bold else 0}"
    if "italic" in word_rule:
        prefix += f"\\i{-1 if italic else 0}"

    styled = f"{{{prefix}}}" if prefix else ""

    char_rules = config.get("custom_char_rules", [])
    animation = word_rule.get("animation", config["animation"])
    current_color: str | None = None
    current_size: int | None = None

    for index, character in enumerate(text):
        char_color = base_color
        char_size = size

        for rule in char_rules:
            if (index + 1) in rule["positions"] or (index - len(text)) in rule["positions"]:
                if "color" in rule:
                    char_color = hex_to_ass_color(rule["color"])
                if "size" in rule:
                    char_size = rule["size"]
                break

        tags = ""
        # ASS only needs a tag when the value changes, so consecutive letters with the
        # same colour do not repeat it.
        if char_color != current_color:
            tags += f"\\c{char_color}"
            current_color = char_color
        if char_size != current_size:
            tags += f"\\fs{char_size}"
            current_size = char_size

        # Typewriter reveals letters one at a time, so it needs per-letter timings.
        if is_active and animation == "typewriter":
            step = max(0, duration_ms) / max(1, len(text))
            start_ms = offset_ms + int(index * step)
            tags += rf"\alpha&HFF&\t({start_ms},{start_ms + 10},\alpha&H00&)"

        if tags:
            styled += f"{{{tags}}}"
        styled += character

    # The typing caret: shown while this word types, then taken away again. It is the
    # bar glyph stretched wide rather than a drawn box, because a drawing lands at a
    # height libass derives from the font instead of sitting on the baseline.
    if is_active and animation == "typewriter" and text:
        typed_from = max(0, offset_ms)
        typed_until = typed_from + max(0, duration_ms)
        styled += (
            "{\\alpha&HFF&"
            f"\\c{base_color}\\fscx220"
            f"\\t({typed_from},{typed_from + 10},\\alpha&H00&)"
            f"\\t({typed_until},{typed_until + 90},\\alpha&HFF&)"
            "}|"
        )

    # Reset to the global style so this word cannot affect the words after it. The
    # angle goes back to the line's own rotation, and only when there is something to
    # put back: a straight line whose entrance never tilted a word carries no angle at
    # all, and writing one would straighten a line that was never crooked.
    reset = (
        f"\\alpha&H00&\\c{hex_to_ass_color(config['font_color'])}"
        f"\\fs{_scaled_font_size(config, config['font_size'])}"
        "\\fscx100\\fscy100\\blur0"
    )
    rotation = round(float(config.get("rotation", 0)), 2)
    if rotation or "\\frz" in get_animation_tag(animation):
        reset += f"\\frz{rotation:g}"
    if "font_name" in word_rule:
        reset += f"\\fn{resolve_font_name(config['font_name'])}"
    if "bold" in word_rule:
        reset += f"\\b{-1 if config['bold'] else 0}"
    if "italic" in word_rule:
        reset += f"\\i{-1 if config['italic'] else 0}"

    return f"{styled}{{{reset}}}"


def _word_rule(index: int, total: int, config: dict[str, Any]) -> dict[str, Any]:
    """The whole-word override for position ``index`` (1-based) or last (negative)."""
    for rule in config.get("custom_word_rules", []):
        if (index + 1) in rule["positions"] or (index - total) in rule["positions"]:
            return dict(rule)
    return {}


def _dialogue_line(start: float, end: float, text: str) -> str:
    return (
        f"Dialogue: 0,{seconds_to_ass_time(start)},{seconds_to_ass_time(end)},"
        f"WordStyle,{_EVENT_FIELDS}{text}"
    )


def _coloured_rule(index: int, total: int, config: dict[str, Any]) -> dict[str, Any]:
    """The word's own rule, given the rainbow colour its position earns it if asked."""
    rule = _word_rule(index, total, config)
    if config.get("rainbow") and "font_color" not in rule:
        return {**rule, "font_color": WORD_RAINBOW[index % len(WORD_RAINBOW)]}
    return rule


def _shown(word: dict[str, Any], uppercase: bool) -> str:
    text = str(word.get("word", "")).strip()
    return text.upper() if uppercase else text


def _full_sentence_line(
    words: list[dict[str, Any]],
    total: int,
    config: dict[str, Any],
    prefix: str,
) -> str:
    """One event carrying the whole sentence, all at once.

    The other modes cut a line into one event per spoken word because the words appear
    one at a time. Here every word is on screen from the first, so chopping the line up
    would replay the entrance once per word; the typewriter still needs each word's own
    window, which is what the offsets below hand it.
    """
    start = float(words[0]["start"])
    end = float(words[-1]["end"]) + TAIL_HOLD_SECONDS
    uppercase = bool(config.get("uppercase", False))

    parts: list[str] = []
    for index, word in enumerate(words):
        text = _shown(word, uppercase)
        if not text:
            continue
        rule = _coloured_rule(index, total, config)
        animation = str(rule.get("animation", config["animation"]))
        word_start = float(word["start"])
        formatted = format_word(
            text,
            config,
            rule,
            True,
            int((float(word["end"]) - word_start) * 1000),
            int((word_start - start) * 1000),
        )
        tag = get_animation_tag(animation)
        parts.append(f"{tag}{formatted}{{\\fscx100\\fscy100}}")

    return _dialogue_line(start, end, f"{prefix}{' '.join(parts)}")


def build_ass(transcript: list[dict[str, Any]], config: dict[str, Any]) -> str:
    """Render the whole transcript into one ASS document."""
    font_name = resolve_font_name(config["font_name"])
    base = hex_to_ass_style_color(config["font_color"])
    outline = _style_outline_color(config)
    shadow = hex_to_ass_style_color(config["shadow_color"])
    prefix, alignment = _position_prefix(config)
    bold = -1 if config["bold"] else 0
    italic = -1 if config["italic"] else 0
    mode = config["display_mode"]
    uppercase = bool(config.get("uppercase", False))
    font_size = _scaled_font_size(config, config["font_size"])

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {VIDEO_WIDTH}",
        f"PlayResY: {VIDEO_HEIGHT}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: WordStyle,{font_name},{font_size},{base},"
        f"&H00FFFFFF,{outline},{shadow},{bold},{italic},0,0,100,100,0,0,"
        f"{config['border_style']},{config['outline']},{config['shadow']},{alignment},10,10,50,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    events: list[str] = []
    for block in transcript:
        words = []
        for word in block.get("words", []):
            text = str(word.get("word", "")).strip()
            if text and not _is_filler(text, config):
                words.append(word)
        if not words:
            continue

        total = len(words)
        if mode == "full":
            events.append(_full_sentence_line(words, total, config, prefix))
            continue

        for active_index, active in enumerate(words):
            start = float(active["start"])
            # The next word's start is when this one should yield; the final word of a
            # line has no successor, so it is held for a moment.
            end = (
                float(words[active_index + 1]["start"])
                if active_index + 1 < total
                else float(active["end"]) + TAIL_HOLD_SECONDS
            )
            duration_ms = int((float(active["end"]) - start) * 1000)

            active_rule = _word_rule(active_index, total, config)
            active_animation = str(active_rule.get("animation", config["animation"]))
            split_clip = mode == "karaoke" and animation_clips_line(active_animation)

            parts: list[str] = []
            base_parts: list[str] = []
            for index, word in enumerate(words):
                text = _shown(word, uppercase)
                if not text:
                    continue

                rule = _coloured_rule(index, total, config)
                is_active = index == active_index
                formatted = format_word(text, config, rule, is_active, duration_ms)
                hidden = f"{{\\alpha&HFF&\\fscx100\\fscy100}}{formatted}{{\\alpha&H00&}}"
                shown = f"{{\\fscx100\\fscy100}}{formatted}"

                if split_clip:
                    # Two lines that share one layout: the base line keeps the sentence
                    # on screen while the reveal line uncovers the active word alone.
                    if is_active:
                        animation = get_animation_tag(active_animation)
                        # No clip reset here: on this line the clip is meant to run,
                        # and the words it would otherwise reach are invisible.
                        parts.append(f"{animation}{formatted}{{\\fscx100\\fscy100}}")
                        base_parts.append(hidden)
                    else:
                        parts.append(hidden)
                        base_parts.append(shown)
                elif is_active:
                    animation = get_animation_tag(active_animation)
                    # The trailing reset inside the word puts alpha and colour back, and
                    # this puts scale back, so the effect stays on this word alone
                    # instead of carrying over to the rest of the line.
                    parts.append(f"{animation}{formatted}{{\\fscx100\\fscy100}}")
                elif mode == "accumulate":
                    # Words already said stay on screen; the ones still to come are drawn
                    # but invisible, so they hold their place and the line never shifts.
                    parts.append(shown if index < active_index else hidden)
                elif mode == "karaoke":
                    parts.append(f"{{\\fscx100\\fscy100}}{formatted}")
                # single_word draws nothing for the inactive words.

            if split_clip:
                events.append(_dialogue_line(start, end, f"{prefix}{' '.join(base_parts)}"))
            dialogue = "".join(parts) if mode == "single_word" else " ".join(parts)
            events.append(_dialogue_line(start, end, f"{prefix}{dialogue}"))

    return "\n".join([*header, *events]) + "\n"
