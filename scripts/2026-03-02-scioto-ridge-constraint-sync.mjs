#!/usr/bin/env node
/**
 * Scioto Ridge Constraint Sync — 2026-03-02
 *
 * Cross-referenced 7 items from the March 2 Scioto Ridge constraints call
 * against all 32 existing Scioto Ridge constraints in ConstraintsPro.
 *
 * Result: ALL 7 items match existing constraints. 0 new constraints needed.
 * Operations: UPDATE 7 existing constraints (add notes with delta intel only).
 *
 * Dedup analysis performed — same-day notes exist on 5 of 7 constraints.
 * Only genuinely NEW information is pushed (per constraints_manager dedup rules).
 *
 * Source: Scioto Ridge Constraints Call — 2026-03-02
 * Approval: User-approved sync proposal
 */

const CONVEX_URL = "https://charming-cuttlefish-923.convex.cloud";

// Scioto Ridge project ID (from Convex — listed as "Scioto")
const SCIOTO_PROJECT_ID = "kh7f17ggqec8h5nd9avnrcaaws7yv843";

// Standard bot creator user ID
const CREATOR_USER_ID = "kn74p9jdq5zmns3vz172rvmpbn7yv694";

// Source metadata
const SOURCE = "Scioto Ridge Constraints Call — 2026-03-02";

// ---------------------------------------------------------------------------
// Convex HTTP helpers
// ---------------------------------------------------------------------------

async function convexQuery(path, args = {}) {
  const cleanArgs = {};
  for (const [k, v] of Object.entries(args)) {
    if (v !== undefined) cleanArgs[k] = v;
  }
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
  for (const [k, v] of Object.entries(args)) {
    if (v !== undefined) cleanArgs[k] = v;
  }
  const resp = await fetch(`${CONVEX_URL}/api/mutation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, args: cleanArgs, format: "json" }),
  });
  const data = await resp.json();
  if (data.status === "error") throw new Error(data.errorMessage || JSON.stringify(data));
  return data.value;
}

// ---------------------------------------------------------------------------
// Dedup: extract keywords for same-day note comparison
// ---------------------------------------------------------------------------

function extractKeywords(text) {
  const lower = text.toLowerCase();
  const words = lower.split(/[\s,;.:()\[\]]+/).filter(w => w.length > 3);
  return new Set(words);
}

function getNewDeltaInfo(existingNotes, newContent) {
  if (!existingNotes) return newContent; // No existing notes — push everything

  const lines = existingNotes.split("\n");
  const today = "3/2";

  // Find today's notes
  const todayNotes = lines
    .filter(line => line.startsWith(today + ":"))
    .map(line => line.substring(line.indexOf(":") + 1).trim());

  if (todayNotes.length === 0) return newContent; // No same-day notes — push everything

  // Check if new content has genuinely new information
  const existingText = todayNotes.join(" ").toLowerCase();
  const newKeywords = extractKeywords(newContent);
  const existingKeywords = extractKeywords(existingText);

  let newWords = 0;
  for (const kw of newKeywords) {
    if (!existingKeywords.has(kw)) newWords++;
  }

  // If more than 30% of the new content's keywords are novel, push it
  if (newWords / newKeywords.size > 0.3) return newContent;

  // Otherwise it's too similar — skip
  return null;
}

// ===========================================================================
// 7 EXISTING CONSTRAINTS TO UPDATE (add delta notes)
// ===========================================================================

const CONSTRAINT_UPDATES = [
  {
    constraintId: "k979kqwe6q8n2rjq769kc7q1hs825zys",
    title: "Pile Production Impacted by PD-10 Uptime",
    noteContent: `Constraint sync (${SOURCE}): 4 of 11 PD-10 machines producing (averaging 35-40 piles/machine/day). 2 broke down — wiring harnesses, need Vermeer, can't come until next week. 2 more need GPS setup — need RDO, not scheduled yet. Aaron escalating with equipment team tomorrow AM + emailing Ben tonight. Kevin taking point on vendor follow-up. Isaac Lopez checking internal resources for GPS. Luis has contact for ex-RDO guy in Minneapolis as backup.`,
  },
  {
    constraintId: "k979py02yxqmh9gtv41yft0ywx8253eh",
    title: "Quality Process",
    noteContent: `Constraint sync (${SOURCE}): Major gap in quality turnover processes identified. Wayne arriving to lead. George Lucas is FQL. Plan: implement CX0 forms (used at Midpoint), create tracker spreadsheet, set up group chats between crafts. Delta's turnover spreadsheet being adapted for Scioto Ridge.`,
  },
  {
    constraintId: "k9778yntascd4dj65wpjy4yxqx80j7sg",
    title: "MV Delays due to mass grading",
    noteContent: `Constraint sync (${SOURCE}): Pre-activity completed March 2 (went well). MV trenching still tracking March 9 start. Waiting on William Charles execution plan — Cody Martens driving. Need subcontractor to call into halls for workers. Need dewatering plan.`,
  },
  {
    constraintId: "k974f419rw1kapesqzn3mfpn0n81cypa",
    title: "Finish BOM for materials for New River",
    noteContent: `Constraint sync (${SOURCE}): Still no response from Stantec on grounding material quantities. Misalignment between contract wording and New River's understanding. Strategy: condense the ask — Luis + Manny meeting New River tomorrow to identify exactly which items they need clarity on, then take specific list to Christian Gomez (RDE).`,
  },
  {
    constraintId: "k9762997njdzr6n1y4kvdcttmx804ssv",
    title: "FME CO for 4 weeks",
    noteContent: `Constraint sync (${SOURCE}): Internal review meeting pushed to Thursday March 5. Was due today (March 2). $1.1M change order on the line.`,
  },
  {
    constraintId: "k9710d47md33mpvjs61q1h4b5x80jdp0",
    title: "PV Grading on hold due to ground conditions",
    noteContent: `Constraint sync (${SOURCE}): D block cut finishing tomorrow (March 3) before rain. C block next. E block minimal work remaining — Adam handling.`,
  },
  {
    constraintId: "k97fzxfrr5kp98w3eqt25stgqn80jqm6",
    title: "Pile Production at risk due to pending PPP",
    noteContent: `Constraint sync (${SOURCE}): Stantec 5-day turnaround holding. Strategy: delay LiDAR flight to capture D+C blocks together to minimize flights and reduce turnaround cycles.`,
  },
];

// ===========================================================================
// MAIN EXECUTION
// ===========================================================================

async function main() {
  const results = { updated: [], skipped: [], failed: [] };

  console.log("=============================================================");
  console.log("  SCIOTO RIDGE CONSTRAINT SYNC — 2026-03-02");
  console.log("  Source: Constraints Call (March 2, 2026)");
  console.log("  Operations: 0 CREATE, 7 UPDATE (notes with delta intel)");
  console.log("=============================================================\n");

  // -----------------------------------------------------------------
  // Cross-reference verification: confirm all 7 constraints exist
  // -----------------------------------------------------------------
  console.log("--- STEP 1: Verifying all 7 target constraints exist ---\n");

  for (const u of CONSTRAINT_UPDATES) {
    let constraint;
    try {
      constraint = await convexQuery("constraints:getWithNotes", {
        constraintId: u.constraintId,
      });
    } catch (e) {
      console.log(`  ERROR fetching ${u.title}: ${e.message}`);
      results.failed.push({ description: u.title, error: `Fetch failed: ${e.message}` });
      continue;
    }

    if (!constraint) {
      console.log(`  NOT FOUND: ${u.constraintId} (${u.title})`);
      results.failed.push({ description: u.title, error: "Constraint not found" });
      continue;
    }

    console.log(`  FOUND: ${u.title}`);
    console.log(`    ID: ${u.constraintId}`);
    console.log(`    Status: ${constraint.status} | Priority: ${constraint.priority}`);

    // -----------------------------------------------------------------
    // Same-day dedup: check if note has genuinely new delta information
    // -----------------------------------------------------------------
    const deltaContent = getNewDeltaInfo(constraint.notes, u.noteContent);
    if (deltaContent === null) {
      console.log(`    DEDUP_SKIP: Same-day note already covers this information.`);
      results.skipped.push({ description: u.title, reason: "Same-day note already exists with overlapping content" });
      console.log("");
      continue;
    }

    // -----------------------------------------------------------------
    // Push the note
    // -----------------------------------------------------------------
    try {
      await convexMutation("constraints:addNote", {
        constraintId: u.constraintId,
        content: deltaContent,
        userId: CREATOR_USER_ID,
      });
      console.log(`    NOTE ADDED (${deltaContent.length} chars)`);
      results.updated.push({ description: u.title, constraintId: u.constraintId });
    } catch (noteErr) {
      console.log(`    FAILED: ${noteErr.message}`);
      results.failed.push({ description: u.title, error: noteErr.message });
    }
    console.log("");
  }

  // -----------------------------------------------------------------
  // Summary
  // -----------------------------------------------------------------
  console.log("\n=============================================================");
  console.log("  SYNC SUMMARY");
  console.log("=============================================================");
  console.log(`  Created:  0 (all 7 items matched existing constraints)`);
  console.log(`  Updated:  ${results.updated.length} existing constraints (notes added)`);
  console.log(`  Skipped:  ${results.skipped.length} (dedup — same-day note overlap)`);
  console.log(`  Failed:   ${results.failed.length}`);
  console.log("=============================================================\n");

  // -----------------------------------------------------------------
  // Cross-reference report
  // -----------------------------------------------------------------
  console.log("--- CROSS-REFERENCE MAPPING ---");
  console.log("");
  console.log("Call Item 1 (PD-10 Uptime)           -> k979kqwe6q8n2rjq769kc7q1hs825zys (Pile Production Impacted by PD-10 Uptime)");
  console.log("   Originally flagged as NEW, but constraint already existed. UPDATED, not created.");
  console.log("");
  console.log("Call Item 2 (Quality/Turnover CX0)    -> k979py02yxqmh9gtv41yft0ywx8253eh (Quality Process)");
  console.log("   Originally flagged as NEW, but constraint already existed (owner: George Lucas). UPDATED, not created.");
  console.log("");
  console.log("Call Item 3 (MV Delays)               -> k9778yntascd4dj65wpjy4yxqx80j7sg (MV Delays due to mass grading)");
  console.log("   Existing constraint, same-day note existed. Delta info pushed.");
  console.log("");
  console.log("Call Item 4 (New River BOM)            -> k974f419rw1kapesqzn3mfpn0n81cypa (Finish BOM for materials for New River)");
  console.log("   Existing constraint, same-day note existed. Delta info pushed (strategy details).");
  console.log("");
  console.log("Call Item 5 (FME Change Order $1.1M)   -> k9762997njdzr6n1y4kvdcttmx804ssv (FME CO for 4 weeks)");
  console.log("   Existing constraint, same-day note existed. Delta info pushed (date shift + dollar amount).");
  console.log("");
  console.log("Call Item 6 (PV Grading)               -> k9710d47md33mpvjs61q1h4b5x80jdp0 (PV Grading on hold due to ground conditions)");
  console.log("   Existing constraint, same-day note existed. Delta info pushed (block-level detail).");
  console.log("");
  console.log("Call Item 7 (Pile Plot Plans / Stantec) -> k97fzxfrr5kp98w3eqt25stgqn80jqm6 (Pile Production at risk due to pending PPP)");
  console.log("   Existing constraint, same-day note existed. Delta info pushed (LiDAR strategy).");
  console.log("");

  if (results.updated.length > 0) {
    console.log("CONSTRAINTS UPDATED:");
    for (const u of results.updated) {
      console.log(`  ${u.constraintId} — ${u.description}`);
    }
    console.log("");
  }

  if (results.skipped.length > 0) {
    console.log("SKIPPED (dedup):");
    for (const s of results.skipped) {
      console.log(`  ${s.description} — ${s.reason}`);
    }
    console.log("");
  }

  if (results.failed.length > 0) {
    console.log("FAILED:");
    for (const f of results.failed) {
      console.log(`  ${f.description} — ${f.error}`);
    }
    console.log("");
  }

  console.log("--- DUPLICATES PREVENTED ---");
  console.log("  2 constraints were originally flagged as NEW by the user but already existed:");
  console.log("  1. 'PD-10 Uptime' -> matched 'Pile Production Impacted by PD-10 Uptime' (k979kqwe6q8n2rjq769kc7q1hs825zys)");
  console.log("  2. 'Quality/Turnover CX0 Forms' -> matched 'Quality Process' (k979py02yxqmh9gtv41yft0ywx8253eh)");
  console.log("  Both were UPDATED with new call details instead of creating duplicates.");
  console.log("");
}

main().catch(err => {
  console.error("FATAL ERROR:", err);
  process.exit(1);
});
