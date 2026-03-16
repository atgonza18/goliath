"""Higgsfield AI — Telegram handler for image and video generation.

Message patterns detected (case-insensitive):
  "generate an image of X"         → image generation, Nano Banana Pro
  "generate image of X"            → image generation, Nano Banana Pro
  "create an image of X"           → image generation, Nano Banana Pro
  "generate a video of X"          → video generation, Kling 3.0 (default)
  "generate video of X"            → video generation, Kling 3.0 (default)
  "create a video of X"            → video generation, Kling 3.0 (default)
  "generate a video of X using M"  → video generation, model M
  "generate a video of X with M"   → video generation, model M

The handler is invoked early in claude_message_handler (before the
orchestrator) so results are returned immediately rather than going through
the full Claude agent pipeline.  Returns True if it handled the message so
the caller can skip the orchestrator.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from bot.services.higgsfield_service import (
    HiggsfieldService,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_VIDEO_MODEL,
    VIDEO_MODELS,
    _normalise_model_name,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for intent detection
# ---------------------------------------------------------------------------
_IMG_PATTERN = re.compile(
    r'(?:generate|create|make|draw|paint)\s+(?:me\s+)?(?:an?\s+)?(?:image|picture|photo)\s+(?:of\s+)?(.+)',
    re.IGNORECASE | re.DOTALL,
)

_VID_PATTERN = re.compile(
    r'(?:generate|create|make)\s+(?:me\s+)?(?:an?\s+)?video\s+(?:of\s+)?(.+)',
    re.IGNORECASE | re.DOTALL,
)

# Model name tail: "using Kling 3.0" or "with Kling 3.0" or "in Kling 3.0"
_MODEL_TAIL = re.compile(
    r'\s+(?:using|with|via|in)\s+(.+?)$',
    re.IGNORECASE,
)

# Build a pattern that matches any known video model name
_ALL_VIDEO_MODEL_NAMES = sorted(VIDEO_MODELS.keys(), key=len, reverse=True)
_VIDEO_MODEL_RE = re.compile(
    r'\b(' + '|'.join(re.escape(m) for m in _ALL_VIDEO_MODEL_NAMES) + r')\b',
    re.IGNORECASE,
)

# Human-readable video model names for the help message
VIDEO_MODEL_DISPLAY = [
    "Kling 3.0",
    "Sora 2",
    "Veo 3.1",
    "Wan 2.5",
    "Seedance 1.5 Pro",
]


def detect_image_intent(text: str) -> Optional[str]:
    """Return the image prompt if the message is an image generation request.

    Returns the prompt string, or None if not matched.
    """
    m = _IMG_PATTERN.search(text.strip())
    if not m:
        return None
    return m.group(1).strip()


def detect_video_intent(text: str) -> Optional[tuple[str, str]]:
    """Return (prompt, model_name) if the message is a video generation request.

    Checks for an inline model specification ("using Kling 3.0").
    Falls back to DEFAULT_VIDEO_MODEL if no model specified.

    Returns (prompt, model_name) or None.
    """
    m = _VID_PATTERN.search(text.strip())
    if not m:
        return None

    rest = m.group(1).strip()

    # Check for "using/with MODEL" tail
    model_name = DEFAULT_VIDEO_MODEL
    tail_match = _MODEL_TAIL.search(rest)
    if tail_match:
        candidate = tail_match.group(1).strip()
        norm = _normalise_model_name(candidate)
        if norm in VIDEO_MODELS:
            model_name = norm
            rest = rest[: tail_match.start()].strip()
        else:
            # Try partial match anywhere in rest
            vm_match = _VIDEO_MODEL_RE.search(rest)
            if vm_match:
                model_name = _normalise_model_name(vm_match.group(1))
                rest = (_VIDEO_MODEL_RE.sub("", rest)
                        .replace("using", "").replace("with", "")
                        .replace("via", "").strip())
    else:
        # Try inline model anywhere in rest (e.g. "a sunset using Kling 3.0")
        vm_match = _VIDEO_MODEL_RE.search(rest)
        if vm_match:
            model_name = _normalise_model_name(vm_match.group(1))
            rest = (_VIDEO_MODEL_RE.sub("", rest)
                    .replace("using", "").replace("with", "")
                    .replace("via", "").strip())

    prompt = rest.strip(" ,.")
    return prompt, model_name


async def handle_higgsfield(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    service: HiggsfieldService,
) -> bool:
    """Detect and handle Higgsfield image/video generation requests.

    Returns True if the message was handled (caller should skip orchestrator).
    Returns False if the message doesn't match any generation pattern.
    """
    text = update.message.text or ""
    chat_id = update.effective_chat.id

    # --- Image generation ---
    img_prompt = detect_image_intent(text)
    if img_prompt:
        logger.info(
            f"Higgsfield image request from chat_id={chat_id}: {img_prompt[:80]}"
        )
        await _handle_image(update, context, service, img_prompt)
        return True

    # --- Video generation ---
    vid_result = detect_video_intent(text)
    if vid_result:
        vid_prompt, vid_model = vid_result
        logger.info(
            f"Higgsfield video request from chat_id={chat_id}: "
            f"prompt={vid_prompt[:80]!r} model={vid_model!r}"
        )
        await _handle_video(update, context, service, vid_prompt, vid_model)
        return True

    return False


async def _handle_image(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    service: HiggsfieldService,
    prompt: str,
) -> None:
    """Run image generation and send the result back to the user."""
    status_msg = await update.message.reply_text(
        "Generating image with Nano Banana Pro... this usually takes 20-60 seconds."
    )

    try:
        result = await service.generate_image(prompt=prompt, model=DEFAULT_IMAGE_MODEL)

        if "error" in result:
            await status_msg.edit_text(
                f"Image generation failed: {result['error']}"
            )
            return

        url = result.get("url")
        if not url:
            await status_msg.edit_text(
                "Generation completed but no image URL was returned."
            )
            return

        # Download and send as Telegram photo
        media_path = await service.download_to_temp(url, suffix=".jpg")
        await status_msg.delete()

        if media_path and media_path.is_file():
            try:
                with open(media_path, "rb") as f:
                    await update.message.reply_photo(
                        photo=f,
                        caption=f"Generated: {prompt[:200]}",
                    )
            finally:
                media_path.unlink(missing_ok=True)
        else:
            # Fallback: send URL directly
            await update.message.reply_text(
                f"Image generated:\n{url}\n\nPrompt: {prompt[:200]}"
            )

    except Exception as exc:
        logger.exception("Higgsfield image handler error")
        try:
            await status_msg.edit_text(f"Image generation failed: {str(exc)[:300]}")
        except Exception:
            pass


async def _handle_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    service: HiggsfieldService,
    prompt: str,
    model: str,
) -> None:
    """Run video generation and send the result back to the user."""
    model_display = model.title()
    status_msg = await update.message.reply_text(
        f"Generating video with {model_display}... "
        f"video generation typically takes 1-3 minutes. I'll send it when it's ready."
    )

    try:
        result = await service.generate_video(prompt=prompt, model=model)

        if "error" in result:
            await status_msg.edit_text(
                f"Video generation failed: {result['error']}"
            )
            return

        url = result.get("url")
        if not url:
            await status_msg.edit_text(
                "Generation completed but no video URL was returned."
            )
            return

        # Download and send as Telegram video
        media_path = await service.download_to_temp(url, suffix=".mp4")
        await status_msg.delete()

        if media_path and media_path.is_file():
            try:
                with open(media_path, "rb") as f:
                    await update.message.reply_video(
                        video=f,
                        caption=f"Generated with {model_display}: {prompt[:200]}",
                        supports_streaming=True,
                    )
            finally:
                media_path.unlink(missing_ok=True)
        else:
            # Fallback: send URL directly
            await update.message.reply_text(
                f"Video generated with {model_display}:\n{url}\n\nPrompt: {prompt[:200]}"
            )

    except Exception as exc:
        logger.exception("Higgsfield video handler error")
        try:
            await status_msg.edit_text(f"Video generation failed: {str(exc)[:300]}")
        except Exception:
            pass
