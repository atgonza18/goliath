#!/usr/bin/env node
/**
 * DEPRECATED (2026-02-28): This one-off retry script was used to fix constraints
 * that failed creation due to project name mismatches in the initial manual run.
 * It should NOT be used going forward.
 *
 * The Goliath email pipeline now handles Hauger emails automatically:
 *   - Constraint content -> matched to existing constraints, appended as notes
 *   - Production content -> stored as intel in MemoryStore + project files
 *   - New constraints are NEVER auto-created from Hauger DSC summaries
 *
 * See: telegram-bot/bot/services/constraint_logger.py (process_hauger_email)
 *      telegram-bot/bot/services/email_poller.py (_classify_email: "hauger_update")
 *
 * Original purpose: Retry creating constraints that failed due to project name mismatches.
 * Maps: Blackford -> Blackford Solar, Scioto Ridge -> Scioto, Pecan Prairie -> Pecan Praire
 * Graceland: was skipped (project didn't exist at the time)
 */

const CONVEX_URL = "https://charming-cuttlefish-923.convex.cloud";

async function convexQuery(path, args = {}) {
  const cleanArgs = {};
  for (const [k, v] of Object.entries(args)) { if (v !== undefined) cleanArgs[k] = v; }
  const resp = await fetch(`${CONVEX_URL}/api/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, args: cleanArgs, format: "json" }),
  });
  const data = await resp.json();
  if (data.status === "error") throw new Error(data.errorMessage || JSON.stringify(data));
  return data.value;
}

async function convexMutation(path, args = {}) {
  const cleanArgs = {};
  for (const [k, v] of Object.entries(args)) { if (v !== undefined) cleanArgs[k] = v; }
  const resp = await fetch(`${CONVEX_URL}/api/mutation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, args: cleanArgs, format: "json" }),
  });
  const data = await resp.json();
  if (data.status === "error") throw new Error(data.errorMessage || JSON.stringify(data));
  return data.value;
}

function isSimilar(desc1, desc2) {
  const normalize = s => s.toLowerCase().replace(/[^a-z0-9\s]/g, '').trim();
  const n1 = normalize(desc1);
  const n2 = normalize(desc2);
  const words1 = new Set(n1.split(/\s+/).filter(w => w.length > 3));
  const words2 = new Set(n2.split(/\s+/).filter(w => w.length > 3));
  let overlap = 0;
  for (const w of words1) { if (words2.has(w)) overlap++; }
  const minLen = Math.min(words1.size, words2.size);
  if (minLen === 0) return false;
  return overlap / minLen > 0.5;
}

// Corrected project ID mapping
const PROJECT_IDS = {
  "Blackford Solar": "kh7bnx2gyw32rw1jcx7t5m831x7ytajh",
  "Scioto": "kh7f17ggqec8h5nd9avnrcaaws7yv843",
  "Pecan Praire": "kh7aj8shkz27q763v2ks5ya98580020h",
};

const CREATOR_USER_ID = "kn74p9jdq5zmns3vz172rvmpbn7yv694";

const FAILED_CONSTRAINTS = [
  {
    description: "T-Line pole conflict with Blackford Wind MV line. Keeley to re-mob 3/4.",
    project_name: "Blackford",
    matched_project: "Blackford Solar",
    priority: "HIGH",
    owner: "Keeley",
    need_by_date: "2026-03-04",
    category: "CONSTRUCTION"
  },
  {
    description: "Pile installation on hold due to PPP delays. Delayed 7 days awaiting EOR response; team has escalated — ETA 3/2.",
    project_name: "Blackford",
    matched_project: "Blackford Solar",
    priority: "HIGH",
    owner: "EOR",
    need_by_date: "2026-03-02",
    category: "ENGINEERING"
  },
  {
    description: "Module production impacted by manpower parking availability. Ideal space identified; team coordinating with landowner on cost.",
    project_name: "Blackford",
    matched_project: "Blackford Solar",
    priority: "MEDIUM",
    owner: "Site Team / Landowner",
    need_by_date: null,
    category: "CONSTRUCTION"
  },
  {
    description: "Ground conditions significantly impacting civil, electrical, and pile production. PV grading continues; 250 piles installed this week; MV starting 3/9.",
    project_name: "Scioto Ridge",
    matched_project: "Scioto",
    priority: "HIGH",
    owner: "Site Team",
    need_by_date: null,
    category: "CONSTRUCTION"
  },
  {
    description: "Substation subcontractor missing DMC connectors — long lead procurement risk. Team coordinating with EOR and procurement on next steps.",
    project_name: "Scioto Ridge",
    matched_project: "Scioto",
    priority: "HIGH",
    owner: "EOR / Procurement",
    need_by_date: null,
    category: "PROCUREMENT"
  },
  {
    description: "String wire required to complete project was supposed to be delivered 1/23 and never arrived. Replacement wire has 6-8 week lead time; team coordinating with engineering and logistics to source alternative — potential warranty risk.",
    project_name: "Graceland",
    matched_project: null,  // Not found
    priority: "HIGH",
    owner: "Engineering / Logistics",
    need_by_date: "2026-03-15",
    category: "PROCUREMENT"
  },
  {
    description: "Acceleration CO poses significant upfront cost risk if not resolved. Owner agreed to non-accelerated schedule + $2.4M; finalizing redlines this week.",
    project_name: "Pecan Prairie",
    matched_project: "Pecan Praire",
    priority: "HIGH",
    owner: "Owner / MasTec Commercial",
    need_by_date: null,
    category: "OTHER"
  },
  {
    description: "Changes to substation design pose long-lead procurement risk. Delivery updated from September to November; procurement exploring alternative sourcing options.",
    project_name: "Pecan Prairie",
    matched_project: "Pecan Praire",
    priority: "HIGH",
    owner: "Procurement",
    need_by_date: "2026-11-01",
    category: "PROCUREMENT"
  }
];

function categoryToDiscipline(category) {
  const map = { "PROCUREMENT": "Procurement", "CONSTRUCTION": "Other", "ENGINEERING": "Other", "OTHER": "Other" };
  return map[category] || "Other";
}

async function main() {
  const results = [];

  // Fetch existing constraints for each matched project for duplicate check
  const projectsToCheck = [...new Set(FAILED_CONSTRAINTS.filter(c => c.matched_project).map(c => c.matched_project))];
  const existingByProject = {};

  console.log("=== Fetching existing constraints for dup check ===");
  for (const projName of projectsToCheck) {
    const projId = PROJECT_IDS[projName];
    try {
      const existing = await convexQuery("constraints:listByProject", { projectId: projId });
      existingByProject[projName] = existing || [];
      console.log(`  ${projName}: ${existingByProject[projName].length} existing constraints`);
    } catch (e) {
      console.log(`  ${projName}: ERROR - ${e.message}`);
      existingByProject[projName] = [];
    }
  }

  console.log("\n=== Processing failed constraints ===");
  for (const c of FAILED_CONSTRAINTS) {
    if (!c.matched_project) {
      console.log(`  SKIP (no project in DB): ${c.project_name} — ${c.description.substring(0, 60)}...`);
      results.push({
        description: c.description,
        project_name: c.project_name,
        priority: c.priority,
        status: "failed",
        constraint_id: null,
        note: `Project "${c.project_name}" does not exist in ConstraintsPro`
      });
      continue;
    }

    const projId = PROJECT_IDS[c.matched_project];
    const existing = existingByProject[c.matched_project] || [];
    const activeExisting = existing.filter(e => e.status !== "resolved");

    let isDuplicate = false;
    let dupMatch = null;
    for (const ex of activeExisting) {
      if (isSimilar(c.description, ex.description)) {
        isDuplicate = true;
        dupMatch = ex;
        break;
      }
    }

    if (isDuplicate) {
      console.log(`  DUPLICATE: ${c.description.substring(0, 60)}...`);
      console.log(`    Matches: ${dupMatch.description.substring(0, 60)}...`);
      results.push({
        description: c.description,
        project_name: c.project_name,
        priority: c.priority,
        status: "duplicate",
        constraint_id: dupMatch._id,
        note: `Similar to existing: "${dupMatch.description.substring(0, 80)}..."`
      });
      continue;
    }

    // Create
    try {
      const createArgs = {
        projectId: projId,
        discipline: categoryToDiscipline(c.category),
        description: c.description,
        priority: c.priority.toLowerCase(),
        owner: c.owner,
        status: "open",
        userId: CREATOR_USER_ID,
      };
      if (c.need_by_date) {
        createArgs.dueDate = new Date(c.need_by_date).getTime();
      }

      const constraintId = await convexMutation("constraints:create", createArgs);
      console.log(`  CREATED: ${c.description.substring(0, 60)}... -> ${constraintId}`);

      // Add note
      try {
        await convexMutation("constraints:addNote", {
          constraintId: constraintId,
          content: "Auto-logged from email — From: Joshua.Hauger@mastec.com | Subject: DSC - 2/27 Production & Constraints",
          userId: CREATOR_USER_ID,
        });
        console.log(`    Note added.`);
      } catch (noteErr) {
        console.log(`    WARNING: Failed to add note: ${noteErr.message}`);
      }

      results.push({
        description: c.description,
        project_name: c.project_name,
        priority: c.priority,
        status: "created",
        constraint_id: constraintId,
        note: `Matched to project "${c.matched_project}" in ConstraintsPro`
      });
    } catch (createErr) {
      console.log(`  FAILED: ${c.description.substring(0, 60)}... — ${createErr.message}`);
      results.push({
        description: c.description,
        project_name: c.project_name,
        priority: c.priority,
        status: "failed",
        constraint_id: null,
        note: `Creation failed: ${createErr.message}`
      });
    }
  }

  console.log("\n=== RETRY SUMMARY ===");
  const created = results.filter(r => r.status === "created").length;
  const duplicates = results.filter(r => r.status === "duplicate").length;
  const failed = results.filter(r => r.status === "failed").length;
  console.log(`Created: ${created} | Duplicates: ${duplicates} | Failed: ${failed}`);
  console.log("\n```json");
  console.log(JSON.stringify(results, null, 2));
  console.log("```");
}

main().catch(err => { console.error("FATAL:", err); process.exit(1); });
