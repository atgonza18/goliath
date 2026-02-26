import logging
import re
import tempfile
from pathlib import Path

import edge_tts

logger = logging.getLogger(__name__)

# Nimrod's voice
VOICE = "en-US-AvaMultilingualNeural"


def _clean_for_speech(text: str) -> str:
    """Strip HTML tags, code blocks, and other markup that shouldn't be spoken."""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Remove code blocks
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    # Remove inline code backticks
    text = re.sub(r"`([^`]*)`", r"\1", text)
    # Remove MEMORY_SAVE and SUBAGENT_REQUEST blocks (shouldn't be here, but just in case)
    text = re.sub(r"```(?:MEMORY_SAVE|SUBAGENT_REQUEST)\s*\n.*?```", "", text, flags=re.DOTALL)
    # Collapse multiple newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def text_to_voice(text: str, max_chars: int = 2000) -> Path | None:
    """
    Convert text to an MP3 voice memo file.

    Returns the path to the generated MP3, or None on failure.
    Truncates text to max_chars to keep voice memos reasonable length.
    """
    clean = _clean_for_speech(text)
    if not clean:
        return None

    # Truncate for reasonable voice memo length (~30s per 500 chars)
    if len(clean) > max_chars:
        clean = clean[:max_chars] + "... that's the gist of it."

    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()

        communicate = edge_tts.Communicate(clean, VOICE)
        await communicate.save(tmp.name)

        logger.info(f"Voice memo generated: {Path(tmp.name).stat().st_size / 1024:.1f} KB")
        return Path(tmp.name)

    except Exception:
        logger.exception("Failed to generate voice memo")
        return None
