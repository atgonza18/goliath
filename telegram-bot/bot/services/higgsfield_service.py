"""Higgsfield AI — image and video generation service.

Wraps the official higgsfield-client SDK for async use inside the Telegram bot.

Supported operations:
  - generateImage(prompt, model?)  — text-to-image, default: Nano Banana Pro
  - generateVideo(prompt, model)   — image-to-video via text-to-video pipeline
      Supported video models: Kling 3.0, Sora 2, Veo 3.1, Wan 2.5, Seedance 1.5 Pro

Generation is async (job-based).  We submit the job and poll until complete,
then return the download URL of the generated file.

Authentication:
  The Higgsfield SDK reads the API key from the environment variable HF_KEY
  (as "{key_id}:{key_secret}") or HF_API_KEY + HF_API_SECRET separately.
  We store the key in .env as HIGGSFIELD_API_KEY in "key_id:key_secret" format
  and pass it directly to the AsyncClient as api_key.  The SDK sets the
  Authorization header to 'Key {api_key}'.

Model application identifiers follow the Higgsfield Platform path convention:
  organization/model-name/version[/task-type]
  Some models include a task-type suffix (e.g. bytedance/seedream/v4/text-to-image)
  while others omit it (e.g. higgsfield-ai/soul/standard).
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional
from urllib.request import urlretrieve

from bot.config import HIGGSFIELD_API_KEY

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model application paths (organization/model/version/task)
# These identifiers correspond to the models listed on the Higgsfield platform.
# The SDK posts to https://platform.higgsfield.ai/{application}
# ---------------------------------------------------------------------------

# Image generation models
IMAGE_MODELS: dict[str, str] = {
    # Nano Banana Pro — Higgsfield's flagship image model
    # Official docs path: https://platform.higgsfield.ai/higgsfield-ai/soul/standard
    "nano-banana-pro":  "higgsfield-ai/soul/standard",
    "soul":             "higgsfield-ai/soul/standard",
    # Fallback generic image model (ByteDance Seedream)
    "seedream":         "bytedance/seedream/v4/text-to-image",
    # Reve text-to-image model
    "reve":             "reve/text-to-image",
}

DEFAULT_IMAGE_MODEL = "nano-banana-pro"

# Video generation models
VIDEO_MODELS: dict[str, str] = {
    "kling 3.0":         "kuaishou/kling/v3/text-to-video",
    "kling3.0":          "kuaishou/kling/v3/text-to-video",
    "kling":             "kuaishou/kling/v3/text-to-video",
    "sora 2":            "openai/sora/v2/text-to-video",
    "sora2":             "openai/sora/v2/text-to-video",
    "sora":              "openai/sora/v2/text-to-video",
    "veo 3.1":           "google/veo/v3.1/text-to-video",
    "veo3.1":            "google/veo/v3.1/text-to-video",
    "veo":               "google/veo/v3.1/text-to-video",
    "wan 2.5":           "alibaba/wan/v2.5/text-to-video",
    "wan2.5":            "alibaba/wan/v2.5/text-to-video",
    "wan":               "alibaba/wan/v2.5/text-to-video",
    "seedance 1.5 pro":  "bytedance/seedance/v1.5-pro/text-to-video",
    "seedance1.5pro":    "bytedance/seedance/v1.5-pro/text-to-video",
    "seedance":          "bytedance/seedance/v1.5-pro/text-to-video",
}

DEFAULT_VIDEO_MODEL = "kling 3.0"

# Polling configuration
POLL_INTERVAL_SECS = 5.0
MAX_POLL_ATTEMPTS  = 120  # 10 minutes max


def _normalise_model_name(name: str) -> str:
    """Lower-case and strip extra whitespace from a model name."""
    return " ".join(name.lower().split())


class HiggsfieldService:
    """Async service for Higgsfield AI image and video generation."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or HIGGSFIELD_API_KEY
        self._client = None  # lazy-init (AsyncClient is created on first use)

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _get_client(self):
        """Return (or lazily create) the Higgsfield AsyncClient."""
        if self._client is None:
            try:
                from higgsfield_client import AsyncClient
                # The SDK expects HF_KEY or HF_API_KEY env var, but we can
                # pass the key directly to bypass the env-var lookup.
                self._client = AsyncClient(api_key=self._api_key)
            except ImportError:
                raise RuntimeError(
                    "higgsfield-client is not installed. "
                    "Run: pip install higgsfield-client"
                )
        return self._client

    # ------------------------------------------------------------------
    # Image generation
    # ------------------------------------------------------------------

    async def generate_image(
        self,
        prompt: str,
        model: str = DEFAULT_IMAGE_MODEL,
        aspect_ratio: str = "1:1",
    ) -> dict:
        """Generate an image from a text prompt.

        Args:
            prompt:       Text description of the image to generate.
            model:        One of the IMAGE_MODELS keys (default: nano-banana-pro).
            aspect_ratio: e.g. "1:1", "16:9", "9:16"

        Returns:
            dict with keys:
              - url (str):    Public URL of the generated image.
              - request_id:   Higgsfield request ID.
              - model_used:   Model application path that was called.
        """
        if not self.is_configured:
            return {"error": "HIGGSFIELD_API_KEY is not set in .env"}

        norm = _normalise_model_name(model)
        application = IMAGE_MODELS.get(norm, IMAGE_MODELS["nano-banana-pro"])
        logger.info(f"Higgsfield image generation: model={norm!r} → {application!r}")

        arguments = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
        }

        try:
            client = self._get_client()
            result = await self._submit_and_poll(client, application, arguments)
            url = self._extract_url(result, media_type="image")
            if url is None:
                return {
                    "error": "Generation completed but no image URL in response.",
                    "raw": result,
                }
            return {
                "url": url,
                "request_id": result.get("request_id", ""),
                "model_used": application,
            }
        except Exception as exc:
            logger.exception(f"Higgsfield image generation failed: {exc}")
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Video generation
    # ------------------------------------------------------------------

    async def generate_video(
        self,
        prompt: str,
        model: str = DEFAULT_VIDEO_MODEL,
    ) -> dict:
        """Generate a video from a text prompt.

        Args:
            prompt:  Text description of the video to generate.
            model:   One of the VIDEO_MODELS keys, e.g. "kling 3.0".

        Returns:
            dict with keys:
              - url (str):    Public URL of the generated video.
              - request_id:   Higgsfield request ID.
              - model_used:   Model application path that was called.
        """
        if not self.is_configured:
            return {"error": "HIGGSFIELD_API_KEY is not set in .env"}

        norm = _normalise_model_name(model)
        application = VIDEO_MODELS.get(norm, VIDEO_MODELS[DEFAULT_VIDEO_MODEL])
        logger.info(f"Higgsfield video generation: model={norm!r} → {application!r}")

        arguments = {"prompt": prompt}

        try:
            client = self._get_client()
            result = await self._submit_and_poll(client, application, arguments)
            url = self._extract_url(result, media_type="video")
            if url is None:
                return {
                    "error": "Generation completed but no video URL in response.",
                    "raw": result,
                }
            return {
                "url": url,
                "request_id": result.get("request_id", ""),
                "model_used": application,
            }
        except Exception as exc:
            logger.exception(f"Higgsfield video generation failed: {exc}")
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _submit_and_poll(
        self,
        client,
        application: str,
        arguments: dict,
    ) -> dict:
        """Submit a job and poll until completion. Returns the result JSON."""
        from higgsfield_client import Completed, Failed, NSFW, Cancelled

        controller = await client.submit(application=application, arguments=arguments)
        logger.info(f"Higgsfield job submitted: request_id={controller.request_id}")

        for attempt in range(MAX_POLL_ATTEMPTS):
            await asyncio.sleep(POLL_INTERVAL_SECS)
            status = await controller.status()
            logger.debug(
                f"Higgsfield poll #{attempt + 1}: "
                f"request={controller.request_id[:8]} status={type(status).__name__}"
            )

            if isinstance(status, Completed):
                result = await controller.get()
                logger.info(
                    f"Higgsfield job completed: request_id={controller.request_id}"
                )
                return result

            if isinstance(status, Failed):
                raise RuntimeError(
                    f"Higgsfield job failed (request_id={controller.request_id})"
                )
            if isinstance(status, NSFW):
                raise RuntimeError(
                    f"Higgsfield flagged prompt as NSFW (request_id={controller.request_id})"
                )
            if isinstance(status, Cancelled):
                raise RuntimeError(
                    f"Higgsfield job was cancelled (request_id={controller.request_id})"
                )

        raise TimeoutError(
            f"Higgsfield job timed out after "
            f"{MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECS:.0f}s "
            f"(request_id={controller.request_id})"
        )

    @staticmethod
    def _extract_url(result: dict, media_type: str = "image") -> Optional[str]:
        """Extract the media URL from a Higgsfield result payload.

        Higgsfield returns result JSON with different shapes depending on the
        model.  We probe several known locations:
          - result["images"][0]["url"]   (image models)
          - result["image"]["url"]       (image models alternate)
          - result["video"]["url"]       (video models)
          - result["url"]                (flat)
          - result["output"][0]          (some models)
        """
        if not isinstance(result, dict):
            return None

        # Video locations
        if media_type == "video":
            vid = result.get("video") or {}
            if isinstance(vid, dict) and vid.get("url"):
                return vid["url"]
            if isinstance(vid, str):
                return vid

        # Image locations
        images = result.get("images")
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict):
                return first.get("url") or first.get("image_url")
            if isinstance(first, str):
                return first

        img = result.get("image") or {}
        if isinstance(img, dict) and img.get("url"):
            return img["url"]
        if isinstance(img, str):
            return img

        # Generic flat
        if result.get("url"):
            return result["url"]

        # Some models return output list
        output = result.get("output")
        if isinstance(output, list) and output:
            return output[0]
        if isinstance(output, str):
            return output

        return None

    async def download_to_temp(self, url: str, suffix: str = ".jpg") -> Optional[Path]:
        """Download a URL to a temporary file and return the Path.

        The caller is responsible for cleaning up the file.
        """
        try:
            import aiohttp
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.close()
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    resp.raise_for_status()
                    with open(tmp.name, "wb") as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            f.write(chunk)
            return Path(tmp.name)
        except Exception:
            logger.exception(f"Failed to download Higgsfield media from {url[:80]}")
            return None

    def list_image_models(self) -> list[str]:
        """Return display names for available image models."""
        return list({v: k for k, v in IMAGE_MODELS.items()}.values())

    def list_video_models(self) -> list[str]:
        """Return human-readable display names for available video models."""
        # Return canonical names (entries without digits-only normalised keys)
        canonical = []
        seen_apps = set()
        for name, app in VIDEO_MODELS.items():
            if app not in seen_apps and " " in name:
                canonical.append(name)
                seen_apps.add(app)
        return canonical


# ---------------------------------------------------------------------------
# Module-level singleton (mirrors the recall_service pattern)
# ---------------------------------------------------------------------------
_service: Optional[HiggsfieldService] = None


def get_higgsfield_service() -> HiggsfieldService:
    """Return the module-level HiggsfieldService singleton."""
    global _service
    if _service is None:
        _service = HiggsfieldService()
    return _service
