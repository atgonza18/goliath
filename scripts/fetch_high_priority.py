#!/usr/bin/env python3
"""Fetch all high-priority constraints from ConstraintsPro via Convex HTTP API."""

import json
import urllib.request
from datetime import datetime

CONVEX_URL = "https://charming-cuttlefish-923.convex.cloud"

def query(path, args=None):
    """Execute a Convex query."""
    payload = json.dumps({"path": path, "args": args or {}}).encode()
    req = urllib.request.Request(
        f"{CONVEX_URL}/api/query",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    if data.get("status") != "success":
        raise RuntimeError(f"Query failed: {data}")
    return data["value"]


def main():
    # 1. Get all projects
    projects = query("projects:list")
    print(f"=== PROJECTS ({len(projects)}) ===")
    for p in projects:
        print(f"  {p['_id']}: {p['name']}")
    print()

    # 2. Get DSC dashboard (all constraints)
    dashboard = query("constraints:getDscDashboard")
    print(f"=== DASHBOARD SUMMARY ===")
    print(json.dumps(dashboard.get("summary", {}), indent=2))
    print()

    # Collect ALL constraints
    all_constraints = []
    for ds in dashboard.get("dscStats", []):
        all_constraints.extend(ds.get("constraints", []))
    all_constraints.extend(dashboard.get("unclaimed", {}).get("constraints", []))

    # Filter high priority, non-resolved
    high_prio = [c for c in all_constraints if c.get("priority") == "high" and c.get("status") != "resolved"]

    print(f"=== HIGH-PRIORITY NON-RESOLVED CONSTRAINTS ({len(high_prio)}) ===\n")

    # Group by project
    by_project = {}
    for c in high_prio:
        pname = c.get("projectName", "Unknown")
        by_project.setdefault(pname, []).append(c)

    now = datetime.now()

    for project_name in sorted(by_project.keys()):
        constraints = by_project[project_name]
        print(f"\n{'='*80}")
        print(f"PROJECT: {project_name} ({len(constraints)} high-priority constraints)")
        print(f"{'='*80}")

        # Sort by due date (closest first)
        def sort_key(c):
            dd = c.get("dueDate")
            if dd:
                return dd
            return float("inf")

        constraints.sort(key=sort_key)

        for i, c in enumerate(constraints, 1):
            print(f"\n--- Constraint #{i} ---")
            print(f"  ID: {c['_id']}")
            print(f"  Description: {c.get('description', 'N/A')}")
            print(f"  Discipline: {c.get('discipline', 'N/A')}")
            print(f"  Status: {c.get('status', 'N/A')}")
            print(f"  Owner: {c.get('owner', 'Unassigned')}")

            # DSC Lead
            dsc_lead = c.get("dscLead")
            if dsc_lead:
                print(f"  DSC Lead: {dsc_lead.get('name', 'N/A')}")
            else:
                print(f"  DSC Lead: UNCLAIMED")

            # Due date
            dd = c.get("dueDate")
            if dd:
                due_dt = datetime.fromtimestamp(dd / 1000)
                print(f"  Need-by Date: {due_dt.strftime('%Y-%m-%d')}")
                days_until = (due_dt - now).days
                if days_until < 0:
                    print(f"  ** OVERDUE by {abs(days_until)} days **")
                else:
                    print(f"  Days until due: {days_until}")
            else:
                print(f"  Need-by Date: NOT SET")

            # Age (days open)
            created_at = c.get("createdAt")
            if created_at:
                created_dt = datetime.fromtimestamp(created_at / 1000)
                age_days = (now - created_dt).days
                print(f"  Created: {created_dt.strftime('%Y-%m-%d')}")
                print(f"  Age (days open): {age_days}")

            # Notes
            notes = c.get("notes")
            if notes:
                print(f"  Notes:")
                for line in notes.split("\n"):
                    print(f"    {line}")
            else:
                print(f"  Notes: None")

            print(f"  Full JSON ID: {c['_id']}")

    # Also print all constraint IDs so we can fetch activity history
    print("\n\n=== ALL HIGH-PRIORITY CONSTRAINT IDs (for activity fetch) ===")
    for c in high_prio:
        print(c["_id"])


if __name__ == "__main__":
    main()
