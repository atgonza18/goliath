from dataclasses import dataclass
from typing import Optional


@dataclass
class Memory:
    id: int
    created_at: str
    category: str
    project_key: Optional[str]
    summary: str
    detail: Optional[str]
    source: str
    tags: Optional[str]
    resolved: int

    @classmethod
    def from_row(cls, row) -> "Memory":
        return cls(
            id=row["id"],
            created_at=row["created_at"],
            category=row["category"],
            project_key=row["project_key"],
            summary=row["summary"],
            detail=row["detail"],
            source=row["source"],
            tags=row["tags"],
            resolved=row["resolved"],
        )
