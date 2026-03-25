"""Lightweight async Convex HTTP client for calling mutations and queries.

Uses the Convex REST API directly (no SDK needed).
The CONVEX_URL is read from environment or defaults to the production deployment.

Usage:
    client = ConvexClient()
    result = await client.mutation("calls:createPendingSyncsBatch", {
        "callId": "abc-123",
        "callDate": 1711111111000,
        "constraints": [...]
    })
"""

import json
import logging
import os
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

# Default Convex deployment URL (production)
_DEFAULT_CONVEX_URL = "https://charming-cuttlefish-923.convex.cloud"


class ConvexClient:
    """Async HTTP client for Convex mutations and queries."""

    def __init__(self, url: Optional[str] = None):
        self._url = url or os.getenv("CONVEX_URL", _DEFAULT_CONVEX_URL)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def mutation(self, path: str, args: dict[str, Any]) -> Any:
        """Call a Convex mutation.

        Args:
            path: Module:function path, e.g. "calls:createPendingSyncsBatch"
            args: Arguments to pass to the mutation

        Returns:
            The mutation result (parsed JSON)
        """
        return await self._call("mutation", path, args)

    async def query(self, path: str, args: dict[str, Any]) -> Any:
        """Call a Convex query.

        Args:
            path: Module:function path, e.g. "calls:listCalls"
            args: Arguments to pass to the query

        Returns:
            The query result (parsed JSON)
        """
        return await self._call("query", path, args)

    async def _call(self, kind: str, path: str, args: dict[str, Any]) -> Any:
        """Make a Convex API call."""
        session = await self._get_session()
        url = f"{self._url}/api/{kind}"

        payload = {
            "path": path,
            "args": args,
            "format": "json",
        }

        try:
            async with session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(
                        f"Convex {kind} {path} failed: HTTP {resp.status}: {body[:500]}"
                    )
                    return None

                result = await resp.json()

                # Convex returns {"status": "success", "value": ...} or {"status": "error", ...}
                if isinstance(result, dict):
                    if result.get("status") == "error":
                        logger.error(
                            f"Convex {kind} {path} error: {result.get('errorMessage', 'unknown')}"
                        )
                        return None
                    return result.get("value", result)

                return result

        except aiohttp.ClientError as e:
            logger.error(f"Convex {kind} {path} network error: {e}")
            return None
        except Exception:
            logger.exception(f"Convex {kind} {path} unexpected error")
            return None


# Singleton instance for the bot process
_client: Optional[ConvexClient] = None


def get_convex_client() -> ConvexClient:
    """Get the shared ConvexClient instance."""
    global _client
    if _client is None:
        _client = ConvexClient()
    return _client


async def push_pending_constraint_syncs(
    call_id: str,
    call_title: str,
    project_name: str,
    call_date: int,
    constraints: list[dict],
    project_id: Optional[str] = None,
) -> Optional[dict]:
    """Push extracted constraints from a call to Convex as pending syncs.

    This is the main integration point between the Goliath transcript_processor
    and the ConstraintsPro Calls review page.

    Args:
        call_id: Recall.ai bot ID or unique call identifier
        call_title: Meeting title/description
        project_name: Project name for display
        call_date: Call timestamp in Unix milliseconds
        constraints: List of constraint dicts, each with:
            - proposedAction: "NEW", "UPDATE", or "CLOSE"
            - constraintData: {description, discipline, priority, owner, notes, ...}
        project_id: Optional Convex project ID (if matched)

    Returns:
        Result dict with {created: N, ids: [...]} or None on failure
    """
    if not constraints:
        logger.debug("push_pending_constraint_syncs: no constraints to push")
        return None

    client = get_convex_client()

    # Build the batch payload
    batch_constraints = []
    for c in constraints:
        # Normalize the constraint data
        proposed_action = c.get("proposedAction", c.get("action", "NEW")).upper()
        if proposed_action not in ("NEW", "UPDATE", "CLOSE"):
            proposed_action = "NEW"

        constraint_data = c.get("constraintData", c)
        batch_constraints.append({
            "proposedAction": proposed_action,
            "constraintData": {
                "description": constraint_data.get("description", ""),
                "discipline": constraint_data.get("discipline", "Other"),
                "priority": constraint_data.get("priority", "medium"),
                "owner": constraint_data.get("owner"),
                "notes": constraint_data.get("notes"),
                "existingConstraintId": constraint_data.get("existingConstraintId"),
                "existingDescription": constraint_data.get("existingDescription"),
            },
        })

    args: dict[str, Any] = {
        "callId": call_id,
        "callTitle": call_title,
        "projectName": project_name,
        "callDate": call_date,
        "constraints": batch_constraints,
    }

    if project_id:
        args["projectId"] = project_id

    try:
        result = await client.mutation("calls:createPendingSyncsBatch", args)
        if result:
            created = result.get("created", 0) if isinstance(result, dict) else 0
            logger.info(
                f"Pushed {created} pending constraint sync(s) to Convex "
                f"for call {call_id[:8]} ({project_name})"
            )
        return result
    except Exception:
        logger.exception(
            f"Failed to push pending constraint syncs to Convex for call {call_id[:8]}"
        )
        return None


async def save_call_transcript(
    call_id: str,
    call_title: str,
    project_name: str,
    call_date: int,
    raw_transcript: str,
    project_id: Optional[str] = None,
) -> Optional[str]:
    """Save a raw meeting transcript to Convex for the Transcripts tab.

    Args:
        call_id: Recall.ai bot ID or unique call identifier
        call_title: Meeting title/description
        project_name: Project name for display
        call_date: Call timestamp in Unix milliseconds
        raw_transcript: Full raw transcript text
        project_id: Optional Convex project ID (if matched)

    Returns:
        Transcript document ID or None on failure
    """
    if not raw_transcript:
        logger.debug("save_call_transcript: empty transcript, skipping")
        return None

    client = get_convex_client()

    args: dict[str, Any] = {
        "callId": call_id,
        "callTitle": call_title,
        "projectName": project_name,
        "callDate": call_date,
        "rawTranscript": raw_transcript,
    }

    if project_id:
        args["projectId"] = project_id

    try:
        result = await client.mutation("calls:saveCallTranscript", args)
        if result:
            logger.info(
                f"Saved transcript to Convex for call {call_id[:8]} "
                f"({project_name}, {len(raw_transcript)} chars)"
            )
        return result
    except Exception:
        logger.exception(
            f"Failed to save transcript to Convex for call {call_id[:8]}"
        )
        return None
