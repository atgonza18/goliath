#!/usr/bin/env python3
"""Poll Recall.ai for Salt Branch transcript — standalone poller.

Bot ID: f935d0c3-2447-4a19-9bf0-cd3122068410
Polls every 60s until transcript is available or max retries hit.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

CT = ZoneInfo("America/Chicago")

BOT_ID = "f935d0c3-2447-4a19-9bf0-cd3122068410"
API_KEY = "0c6672b813840510595fbc7b9ec89a43a871ab58"
BASE_URL = "https://us-west-2.recall.ai"
HEADERS = {
    "Authorization": f"Token {API_KEY}",
    "Content-Type": "application/json",
}

TRANSCRIPT_DIR = Path("/opt/goliath/transcripts/recall")
TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

POLL_INTERVAL = 60  # seconds
MAX_POLLS = 120  # 2 hours max


def log(msg: str):
    ts = datetime.now(CT).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def get_bot_status() -> dict:
    resp = requests.get(f"{BASE_URL}/api/v1/bot/{BOT_ID}/", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_transcript(bot_data: dict) -> str | None:
    """Walk the Recall API chain: bot -> recordings -> media_shortcuts -> download transcript."""
    recordings = bot_data.get("recordings", [])
    if not recordings:
        log("No recordings found on bot.")
        return None

    recording_id = recordings[0] if isinstance(recordings[0], str) else recordings[0].get("id")
    log(f"Recording ID: {recording_id}")

    # Get recording details
    resp = requests.get(f"{BASE_URL}/api/v1/recording/{recording_id}/", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    recording_data = resp.json()

    media_shortcuts = recording_data.get("media_shortcuts", {})
    transcript_info = media_shortcuts.get("transcript", {})
    transcript_data = transcript_info.get("data", {})
    download_url = transcript_data.get("download_url")

    if not download_url:
        log(f"No transcript download URL yet. Transcript status: {transcript_info.get('status', 'unknown')}")
        return None

    log(f"Downloading transcript from {download_url[:80]}...")
    resp = requests.get(download_url, timeout=60)
    resp.raise_for_status()
    transcript_json = resp.json()

    return format_transcript(transcript_json)


def format_transcript(transcript_json: list) -> str:
    """Convert Recall.ai transcript JSON to readable text."""
    if not transcript_json:
        return ""

    lines = []
    current_speaker = None

    for segment in transcript_json:
        participant = segment.get("participant", {})
        speaker_name = participant.get("name", "Unknown Speaker")
        words = segment.get("words", [])

        if not words:
            continue

        text = " ".join(w.get("text", "") for w in words).strip()
        if not text:
            continue

        start_ts = words[0].get("start_timestamp", {})
        relative_secs = start_ts.get("relative", 0)
        h = int(relative_secs // 3600)
        m = int((relative_secs % 3600) // 60)
        s = int(relative_secs % 60)
        timestamp = f"{h:02d}:{m:02d}:{s:02d}"

        if speaker_name != current_speaker:
            current_speaker = speaker_name
            lines.append(f"\n[{timestamp}] {speaker_name}:")

        lines.append(f"  {text}")

    return "\n".join(lines).strip()


def main():
    log(f"Starting Recall transcript poller for bot {BOT_ID[:8]}")
    log(f"Polling every {POLL_INTERVAL}s, max {MAX_POLLS} attempts")

    for attempt in range(1, MAX_POLLS + 1):
        try:
            bot_data = get_bot_status()

            # Get latest status
            status_changes = bot_data.get("status_changes", [])
            status_code = status_changes[-1].get("code", "unknown") if status_changes else "unknown"
            log(f"Poll #{attempt}: status={status_code}")

            if status_code == "fatal":
                sub_code = status_changes[-1].get("sub_code", "unknown") if status_changes else "unknown"
                log(f"❌ Bot FAILED: {sub_code}")
                # Save error info
                error_file = TRANSCRIPT_DIR / f"2026-03-02-{BOT_ID[:8]}-ERROR.txt"
                error_file.write_text(f"Bot failed with status: fatal\nSub-code: {sub_code}\nFull data:\n{json.dumps(bot_data, indent=2)}")
                log(f"Error details saved to {error_file}")
                sys.exit(1)

            if status_code == "done":
                log("✅ Bot is DONE — fetching transcript...")

                transcript_text = get_transcript(bot_data)

                if transcript_text:
                    date_str = datetime.now(CT).strftime("%Y-%m-%d")
                    transcript_file = TRANSCRIPT_DIR / f"{date_str}-{BOT_ID[:8]}.txt"
                    transcript_file.write_text(transcript_text)
                    log(f"✅ Transcript saved: {transcript_file} ({len(transcript_text)} chars)")

                    # Also save raw bot data for debugging
                    raw_file = TRANSCRIPT_DIR / f"{date_str}-{BOT_ID[:8]}-raw.json"
                    raw_file.write_text(json.dumps(bot_data, indent=2))
                    log(f"Raw bot data saved: {raw_file}")

                    # Signal file for orchestrator to pick up
                    signal_file = TRANSCRIPT_DIR / f"{date_str}-{BOT_ID[:8]}-READY.signal"
                    signal_file.write_text(str(transcript_file))
                    log(f"Signal file written: {signal_file}")

                    print(f"\n{'='*60}")
                    print(f"TRANSCRIPT READY: {transcript_file}")
                    print(f"Length: {len(transcript_text)} characters")
                    print(f"{'='*60}")
                    sys.exit(0)
                else:
                    log("Bot is done but transcript not available yet — might still be processing. Continuing to poll...")

            # For any non-terminal status, or done-but-no-transcript, keep polling
            if status_code not in ("fatal",):
                time.sleep(POLL_INTERVAL)
            else:
                break

        except requests.exceptions.RequestException as e:
            log(f"⚠️ Network error (will retry): {e}")
            time.sleep(POLL_INTERVAL)
        except Exception as e:
            log(f"⚠️ Unexpected error (will retry): {e}")
            time.sleep(POLL_INTERVAL)

    log(f"⏰ Max polls reached ({MAX_POLLS}). Giving up.")
    sys.exit(2)


if __name__ == "__main__":
    main()
