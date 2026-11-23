"""Shared timing rules for speech in film plans, direction and audition."""

import unicodedata


NATURAL_SPEECH = (
    '按角色处境和普通交谈的节奏说。重音有变化，连读清楚，句尾自然收住。'
    '迟疑或停顿由眼前事件引起。避免逐字慢念、均匀停顿、拖长尾音或播音腔。'
)
H3_SPEECH = (
    'Use a natural conversational Mandarin pace with connected words and changing emphasis. '
    'Keep any hesitation tied to the scene. Let the last syllable fall naturally.'
)


def speech_units(text):
    return sum(character.isalnum() for character in text)


def narration_fits_draft(text, seconds):
    """Reject only clearly overfilled slots. Actual speech duration controls the edit."""
    return speech_units(text) <= seconds * 5


def normalized_words(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def mandarin_word_match(expected, transcript):
    """Match spoken wording, preserving raw ASR for picture and referent review."""
    script, heard = normalized_words(expected), normalized_words(transcript)
    if not script or not heard:
        return 'mismatch'
    if script == heard:
        return 'exact'
    # These three written pronouns have identical Mandarin pronunciation and tone.
    pronouns = str.maketrans({'她': '他', '它': '他'})
    if script.translate(pronouns) == heard.translate(pronouns):
        return 'pronoun_homophone'
    return 'mismatch'
