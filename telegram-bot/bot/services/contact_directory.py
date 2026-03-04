"""
Contact Directory — maps known senders (email/name) to projects.

This module provides a lightweight, persistent contact-to-project mapping
used by the email reply monitor to pre-filter constraints before matching.

The directory is stored as a JSON file and supports:
  - Initial seeding with known contacts
  - Runtime additions when a user approves a constraint match (learning)
  - Fuzzy name matching (partial match on name parts)

File location: telegram-bot/data/contact_directory.json
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default path for the contact directory JSON file
_DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "contact_directory.json"


class ContactDirectory:
    """Persistent contact-to-project mapping with fuzzy lookup."""

    def __init__(self, path: Path | None = None):
        self._path = path or _DEFAULT_PATH
        self._contacts: list[dict] = []
        self._loaded = False

    def load(self) -> None:
        """Load contact directory from JSON file."""
        if not self._path.exists():
            logger.warning(f"Contact directory not found at {self._path}, starting empty")
            self._contacts = []
            self._loaded = True
            return

        try:
            with open(self._path, "r") as f:
                data = json.load(f)
            self._contacts = data.get("contacts", [])
            self._loaded = True
            logger.info(f"Loaded {len(self._contacts)} contacts from {self._path}")
        except Exception:
            logger.exception(f"Failed to load contact directory from {self._path}")
            self._contacts = []
            self._loaded = True

    def save(self) -> None:
        """Persist the contact directory to JSON file."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {"contacts": self._contacts}
            with open(self._path, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved {len(self._contacts)} contacts to {self._path}")
        except Exception:
            logger.exception(f"Failed to save contact directory to {self._path}")

    def lookup(self, sender: str) -> Optional[str]:
        """Look up a sender's project by email address or name.

        Args:
            sender: Email address or "Name <email>" string from the email.

        Returns:
            Project key (e.g., 'tehuacana') or None if no match found.
        """
        if not self._loaded:
            self.load()

        sender_lower = sender.lower().strip()

        # Extract name and email parts from "Name <email>" format
        sender_name = ""
        sender_email = sender_lower
        if "<" in sender_lower and ">" in sender_lower:
            sender_name = sender_lower.split("<")[0].strip()
            sender_email = sender_lower.split("<")[1].split(">")[0].strip()

        for contact in self._contacts:
            # Match by email address (exact or substring)
            contact_email = contact.get("email", "").lower()
            if contact_email and contact_email in sender_email:
                return contact.get("project")

            if contact_email and sender_email in contact_email:
                return contact.get("project")

            # Match by name (fuzzy — check if contact name parts appear in sender)
            contact_name = contact.get("name", "").lower()
            if not contact_name:
                continue

            # Split contact name into parts and check if significant parts match
            name_parts = [p for p in contact_name.split() if len(p) > 2]
            if not name_parts:
                continue

            # Check against sender email (e.g., "proot@company.com" contains "root")
            # Check against sender display name
            search_text = f"{sender_name} {sender_email}"

            # Require last name match at minimum (last part of the name)
            last_name = name_parts[-1]
            if last_name in search_text:
                # If there's a first name too, check it matches
                if len(name_parts) >= 2:
                    first_name = name_parts[0]
                    # Either first name initial or full first name should appear
                    if first_name in search_text or first_name[0] in sender_email.split("@")[0]:
                        return contact.get("project")
                else:
                    # Single name part matched
                    return contact.get("project")

        return None

    def add_contact(
        self,
        name: str,
        project: str,
        email: str = "",
        scope: str = "",
        source: str = "learned",
    ) -> None:
        """Add or update a contact in the directory.

        Args:
            name: Contact's display name
            project: Project key (e.g., 'tehuacana')
            email: Email address (optional)
            scope: Scope hint like 'equipment', 'general' (optional)
            source: How this entry was added ('seed' or 'learned')
        """
        if not self._loaded:
            self.load()

        # Check if contact already exists (by name or email)
        for existing in self._contacts:
            existing_name = existing.get("name", "").lower()
            existing_email = existing.get("email", "").lower()

            name_match = name.lower() == existing_name if name else False
            email_match = (
                email.lower() == existing_email if email and existing_email else False
            )

            if name_match or email_match:
                # Update existing entry
                if email and not existing.get("email"):
                    existing["email"] = email
                if project:
                    existing["project"] = project
                if scope:
                    existing["scope"] = scope
                existing["source"] = source
                logger.info(f"Updated contact: {name} -> {project}")
                self.save()
                return

        # Add new entry
        entry = {
            "name": name,
            "project": project,
        }
        if email:
            entry["email"] = email
        if scope:
            entry["scope"] = scope
        entry["source"] = source

        self._contacts.append(entry)
        logger.info(f"Added new contact: {name} -> {project}")
        self.save()

    @property
    def contacts(self) -> list[dict]:
        """Return the current contact list (read-only access)."""
        if not self._loaded:
            self.load()
        return list(self._contacts)
