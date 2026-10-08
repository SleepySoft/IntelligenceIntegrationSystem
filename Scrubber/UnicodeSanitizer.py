import re
import unicodedata
from typing import Optional, Literal

try:
    from typing import TypeAlias
    print("Success: typing.TypeAlias is available")
except ImportError:
    from typing_extensions import TypeAlias
    print("Success: Using typing_extensions")


NormalizationForm: TypeAlias = Literal["NFC", "NFD", "NFKC", "NFKD"]


def sanitize_unicode_string(
    text: str,
    max_length: int = 10240,
    normalize_form: NormalizationForm = 'NFKC',
    allow_emoji: bool = False
) -> Optional[str]:
    """
    Sanitizes and cleans input string by removing Unicode variation selectors,
    combining characters, and other potentially dangerous Unicode features.

    Args:
        text: Input string to be sanitized
        max_length: Maximum allowed input length (defense against bomb attacks)
        normalize_form: Unicode normalization form (NFKC recommended). This comments
                        NFC - Normalization Form C
                        NFD - Normalization Form D
                        NFKD - Normalization Form KD
                        NFKC - Normalization Form KC
        allow_emoji: Whether to preserve emoji characters

    Returns:
        Sanitized string or None if input exceeds max_length

    Raises:
        ValueError: If invalid normalization form specified
    """

    # Defense against character bomb attacks
    if len(text) > max_length:
        text = text[:max_length]

    # Unicode normalization (NFKC handles variation selectors and compatibility chars)
    try:
        normalized = unicodedata.normalize(normalize_form, text)
    except ValueError as e:
        raise ValueError(f"Invalid normalization form: {normalize_form}") from e

    # Combined regex pattern for comprehensive filtering
    variation_selector_ranges = (
        r'\u180B-\u180D'                    # Mongolian variation selectors
        r'\uFE00-\uFE0F'                    # Unicode variation selectors
        r'[\uDB40-\uDBFF][\uDC00-\uDFFF]'   # Surrogate pairs handling
    )

    emoji_block = (r'\u1F000-\u1FAFF'   # Basic block
                   r'\u231A-\u231B'     # Watch symbols
                   r'\u23E9-\u23FF'     # Control symbols
                   ) if not allow_emoji else ''

    danger_pattern = re.compile(
        r'['
        r'\u0000-\u001F\u007F-\u009F' + # Control characters
        r'\u0300-\u036F' +              # Combining diacritics
        r'\u200B-\u200D\u202A-\u202E' + # Zero-width/control characters
        r'\uFFF0-\uFFFF'                # Special purpose characters
        + emoji_block +                 # Dynamically excludes Emojis
        variation_selector_ranges +
        r']',
        flags=re.UNICODE
    )

    # Multi-stage sanitization
    sanitized = danger_pattern.sub('', normalized)

    # Secondary validation for remaining variation selectors
    sanitized = re.sub(
        r'[\uFE00-\uFE0F]',  # Final variation selector check
        '',
        sanitized
    )

    return sanitized.strip()
