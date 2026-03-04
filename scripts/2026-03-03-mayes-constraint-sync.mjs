#!/usr/bin/env node
/**
 * Mayes Constraint Sync — 2026-03-03
 *
 * Approved proposal: push transcript-extracted constraints from the
 * Mayes Weekly Constraints Call (2026-03-03) to ConstraintsPro.
 *
 * Operations:
 *   CREATE (1 new constraint)
 *   UPDATE (10 existing constraints — add notes, bump priorities, update fields)
 *   RESOLVE (0 — constraints mentioned as "close it out" were already resolved)
 *
 * Source: 2026-03-03-mayes.txt (Recall.ai transcript)
 * Approval: User-approved sync proposal
 */

const CONVEX_URL = "https://charming-cuttlefish-923.convex.cloud";

// Mayes project ID (from Convex)
const MAYES_PROJECT_ID = "kh70ztr3pf679g0005ag6ekxq57yv7v1";

// Aaron Gonzalez DSC user (standard bot creator)
const CREATOR_USER_ID = "kn74p9jdq5zmns3vz172rvmpbn7yv694";

// Source metadata for notes
const SOURCE = "Mayes Weekly Constraints Call — 2026-03-03";

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
// Dedup helper: check if a same-day note already exists covering this topic
// ---------------------------------------------------------------------------

function hasSameDayNote(existingNotes, newNoteContent) {
  if (!existingNotes) return false;
  const lines = existingNotes.split("\n");
  const today = "3/3";
  const todayNotes = lines.filter(line => line.startsWith(today + ":"));
  if (todayNotes.length === 0) return false;
  const newKeywords = extractKeywords(newNoteContent);
  for (const existingNote of todayNotes) {
    const existingKeywords = extractKeywords(existingNote);
    let overlap = 0;
    for (const kw of newKeywords) {
      if (existingKeywords.has(kw)) overlap++;
    }
    if (overlap >= 3) return true;
  }
  return false;
}

function extractKeywords(text) {
  const lower = text.toLowerCase();
  const words = lower.split(/[\s,;.:()\[\]]+/).filter(w => w.length > 3);
  return new Set(words);
}

// ---------------------------------------------------------------------------
// Similarity check for dedup on new constraints
// ---------------------------------------------------------------------------

function isSimilar(desc1, desc2) {
  const normalize = s => s.toLowerCase().replace(/[^a-z0-9\s]/g, "").trim();
  const n1 = normalize(desc1);
  const n2 = normalize(desc2);
  const words1 = new Set(n1.split(/\s+/).filter(w => w.length > 3));
  const words2 = new Set(n2.split(/\s+/).filter(w => w.length > 3));
  let overlap = 0;
  for (const w of words1) {
    if (words2.has(w)) overlap++;
  }
  const minLen = Math.min(words1.size, words2.size);
  if (minLen === 0) return false;
  return overlap / minLen > 0.5;
}

// ===========================================================================
// 1 NEW CONSTRAINT TO CREATE
// ===========================================================================

const NEW_CONSTRAINTS = [
  {
    description: "Tracker Crew Mobilization Risk — Larry's 40-person tracker crew mobilizing next week could be idle if pile turnover lags. Panda and Larry need 3-week look-ahead coordination meeting to ensure incoming crew has work from day one. Risk of 40+ workers on site with no blocks turned over for tracker install.",
    priority: "high",
    owner: "Larry / Panda",
    category: "Construction",
    status: "open",
    dueDate: null,
  },
];

// ===========================================================================
// 10 EXISTING CONSTRAINTS TO UPDATE (add notes, bump priorities, update fields)
// ===========================================================================

const CONSTRAINT_UPDATES = [
  // --- USER-SPECIFIED UPDATES (items 2-5) ---
  {
    constraintId: "k974y6s6bbm84txvfh6yxa7z8h826xy6",
    title: "Pre-Drilling (Inverter Piles)",
    fieldUpdates: {
      priority: "high",
      owner: "Harry / Richard",
    },
    noteContent: `Constraint sync (${SOURCE}): 3/3 call: Hey Duck drill bits too small for inverter piles. Precision Drilling machine at Salt Branch (Mitch not returning calls). Harry to make priority call on which project gets the machine. Hey Duck is most cost-effective for tracker piles but has limited drill bit size for inverters. Richard reached out to Mitch at Precision — Salt Branch superintendent won't release machines (told to prioritize Salt Branch even though 3 machines sitting idle). Harry will call after meeting to resolve.`,
  },
  {
    constraintId: "k973eererjn927ckqshmcd9dvx826cm5",
    title: "Pile Remediation",
    fieldUpdates: {
      priority: "high",
      owner: "Panda",
    },
    noteContent: `Constraint sync (${SOURCE}): 3/3 call: Cascading into tracker timeline. Production at 750/day targeting 1,700 with 18 rigs. Remediation crew getting more people to catch up. Pull testing is the bottleneck — 72-hour rain delay required. Plan: 2 excavators with vibratory hammers for pull testing, plus dedicated remed crew behind install. Harry recommends planning for 3rd and 4th pull test crews as backup. Cannot let remed be the bottleneck for tracker install.`,
  },
  {
    constraintId: "k9758etg9j2x3mwndgdyrr9cds7zn261",
    title: "Crossing Agreement (Permit)",
    fieldUpdates: {},
    noteContent: `Constraint sync (${SOURCE}): 3/3 call: LRE rejected split-by-utility approach. 4 PM meeting today getting Timmons on phone with LRE permitting specialist. Request was to get Timmons directly talking to LRE's permitting specialist to align. Aaron requested invite to the 4 PM meeting.`,
  },
  {
    constraintId: "k97a26qp9v776nk0pfejbk5cm981r696",
    title: "Army Corps Permit (Jurisdictional Waterway)",
    fieldUpdates: {},
    noteContent: `Constraint sync (${SOURCE}): 3/3 call: Still MIA. 3 downstream impacts identified: (1) Floodplain permit — was supposed to be in lieu of Army Corps, could put project out of compliance with county; (2) Bore under waterway cannot proceed without permit; (3) Lay-down yard in flood zone at risk. Chance committed to last Friday but date came and gone. Juan following up daily. All documentation submitted — entirely in Army Corps court. Team needs to track timeline impact and document what construction areas get cut off if permit does not arrive.`,
  },

  // --- ADDITIONAL UPDATES FROM 3/3 CALL (item 6) ---
  {
    constraintId: "k97awy1my0b4skech55ce8kxxx7zn7k7",
    title: "Shoals Deliveries (Harnesses)",
    fieldUpdates: {},
    noteContent: `Constraint sync (${SOURCE}): 130 LBDs received. First harness deliveries scheduled for 3/20 (320 harnesses). LBDs next week, then BLAs, then harness production delivery 3/13 or 3/20. Electrical team needs to get the delivery schedule forwarded to them for accountability. May delivery schedule not yet documented officially — Aaron to email Mary for it.`,
  },
  {
    constraintId: "k9758mg5ab8rk15hgrh4rfjh4d80yy88",
    title: "PD-10 Production",
    fieldUpdates: {},
    noteContent: `Constraint sync (${SOURCE}): Distro ahead of electrical now with leave-outs. 2 more operators today, 2 more coming — will have 16 by end of week. 2 additional machines expected (on truck arriving today or tomorrow) bringing to 18 rigs total. Current average 750 piles/day. With 18 rigs at 125/rig daily average, targeting 1,700+/day next week (potentially up to 2,000). Cycle times ranging 3.5-9.5 minutes per pile. Harder ground areas hitting 7-8 min. Panda adding distance/time tracking to all machines to identify initial refusals.`,
  },
  {
    constraintId: "k977fgbjy179t9dszsawzd9rvh7zmzss",
    title: "Block P05-1A Access",
    fieldUpdates: {
      priority: "low",
    },
    noteContent: `Constraint sync (${SOURCE}): Timmons OK with the redline proposal Richard drove. Timmons just putting it into an official drawing with the throat revision. Harry and team to have side conversation about cost recovery avenue (similar to bond relocation CEO push-through — change in design/work not originally carried). To be included after 90% true-up. Lowered to LOW priority — no construction constraint remaining.`,
  },
  {
    constraintId: "k972wpfac4fczfrj7jbdgrhsvd81w1jy",
    title: "Welding Subcontractor (Inverter Pile Caps)",
    fieldUpdates: {
      priority: "low",
    },
    noteContent: `Constraint sync (${SOURCE}): Triforce selected. CRT submitted — Juan rejected initially then re-approved this morning. Triforce confirmed availability for Thursday but likely won't weld due to weather. Sean McBride entered the CRT. Dropped to LOW — in procurement pipeline, no longer blocking.`,
  },
  {
    constraintId: "k97brz1t5944pc9dxhj9z945m57zmxr5",
    title: "Piles Delivery Delay",
    fieldUpdates: {
      priority: "low",
    },
    noteContent: `Constraint sync (${SOURCE}): All pile types now received (origin white piles arrived yesterday). Deliveries called off Wed-Fri this week due to storm, but will catch up next week. Change Order 3 finalized yesterday — guaranteed LBD pile delivery by 3/27. Production piles on track. No current concerns on delivery schedule once weather clears. Dropped to LOW.`,
  },
  {
    constraintId: "k978w545581t99zxjfvb4xsyn582744r",
    title: "MV Second Crew",
    fieldUpdates: {},
    noteContent: `Constraint sync (${SOURCE}): About half of the second MV crew arrived today. Remaining experienced crew members arriving Thursday. MV not facing major constraints currently. One RFI outstanding for jacket repair waiting on LRE approval. No replication of Rising Edge substation issues expected — Dustin to verify drawings.`,
  },
];

// ===========================================================================
// MAIN EXECUTION
// ===========================================================================

async function main() {
  const results = { created: [], updated: [], skipped: [], failed: [] };

  console.log("=============================================================");
  console.log("  MAYES CONSTRAINT SYNC — 2026-03-03");
  console.log("  Source: Mayes Weekly Constraints Call (Recall.ai transcript)");
  console.log("=============================================================\n");

  // -----------------------------------------------------------------
  // Step 1: Fetch existing Mayes constraints for dedup
  // -----------------------------------------------------------------
  console.log("--- STEP 1: Fetching existing constraints for dedup ---");
  let existingConstraints;
  try {
    existingConstraints = await convexQuery("constraints:listByProject", {
      projectId: MAYES_PROJECT_ID,
    });
    const open = existingConstraints.filter(c => c.status !== "resolved");
    console.log(`  Found ${existingConstraints.length} total (${open.length} open)\n`);
  } catch (e) {
    console.error("  FATAL: Could not fetch existing constraints:", e.message);
    process.exit(1);
  }

  // -----------------------------------------------------------------
  // Step 2: CREATE 1 new constraint (with dedup check)
  // -----------------------------------------------------------------
  console.log("--- STEP 2: Creating 1 new constraint ---");
  for (const c of NEW_CONSTRAINTS) {
    // Check for duplicates against existing open constraints
    const activeExisting = existingConstraints.filter(e => e.status !== "resolved");
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
      console.log(`  DUPLICATE SKIP: ${c.description.substring(0, 70)}...`);
      console.log(`    Matches existing: "${dupMatch.description.substring(0, 70)}..."`);
      results.skipped.push({
        description: c.description.substring(0, 80),
        reason: `Duplicate of existing: ${dupMatch._id}`,
      });
      continue;
    }

    // Create the constraint
    try {
      const createArgs = {
        projectId: MAYES_PROJECT_ID,
        discipline: c.category,
        description: c.description,
        priority: c.priority,
        owner: c.owner,
        status: c.status,
        userId: CREATOR_USER_ID,
      };
      if (c.dueDate) {
        createArgs.dueDate = new Date(c.dueDate).getTime();
      }

      const constraintId = await convexMutation("constraints:create", createArgs);
      console.log(`  CREATED: ${c.description.substring(0, 70)}...`);
      console.log(`    -> ${constraintId}`);

      // Add source note
      try {
        await convexMutation("constraints:addNote", {
          constraintId,
          content: `Auto-synced from transcript — Source: ${SOURCE}. Harry flagged: need 3-week look-ahead coordination meeting between Panda (piles) and Larry (tracker). Cannot throw 40+ people at a site without confirming block turnover. Drip-feed mobilization preferred.`,
          userId: CREATOR_USER_ID,
        });
        console.log(`    Note added.\n`);
      } catch (noteErr) {
        console.log(`    WARNING: Note failed: ${noteErr.message}\n`);
      }

      results.created.push({
        description: c.description.substring(0, 80),
        constraintId,
        priority: c.priority,
      });
    } catch (createErr) {
      console.log(`  FAILED: ${c.description.substring(0, 70)}...`);
      console.log(`    Error: ${createErr.message}\n`);
      results.failed.push({
        description: c.description.substring(0, 80),
        error: createErr.message,
      });
    }
  }

  // -----------------------------------------------------------------
  // Step 3: UPDATE 10 existing constraints (notes + fields)
  // -----------------------------------------------------------------
  console.log("\n--- STEP 3: Updating 10 existing constraints ---");
  for (const u of CONSTRAINT_UPDATES) {
    console.log(`\n  Processing: ${u.title}`);
    console.log(`  ${"─".repeat(60)}`);

    // Fetch the constraint with its existing notes
    let constraint;
    try {
      constraint = await convexQuery("constraints:getWithNotes", {
        constraintId: u.constraintId,
      });
    } catch (e) {
      console.log(`  ERROR fetching ${u.title}: ${e.message}`);
      results.failed.push({
        description: u.title,
        error: `Fetch failed: ${e.message}`,
      });
      continue;
    }

    if (!constraint) {
      console.log(`  NOT FOUND: ${u.constraintId} (${u.title})`);
      results.failed.push({
        description: u.title,
        error: "Constraint not found",
      });
      continue;
    }

    // Apply field updates (owner, priority, etc.) ALWAYS
    if (u.fieldUpdates && Object.keys(u.fieldUpdates).length > 0) {
      try {
        await convexMutation("constraints:update", {
          constraintId: u.constraintId,
          ...u.fieldUpdates,
          userId: CREATOR_USER_ID,
        });
        console.log(`  FIELDS UPDATED: ${JSON.stringify(u.fieldUpdates)}`);
      } catch (fieldErr) {
        console.log(`  WARNING: Field update failed for ${u.title}: ${fieldErr.message}`);
      }
    }

    // Same-day dedup check for notes
    if (hasSameDayNote(constraint.notes, u.noteContent)) {
      console.log(`  DEDUP_SKIP: ${u.title} — same-day note already exists`);
      results.skipped.push({
        description: u.title,
        reason: "Same-day note already exists (dedup) — field updates still applied",
      });
      continue;
    }

    // Add the note
    try {
      await convexMutation("constraints:addNote", {
        constraintId: u.constraintId,
        content: u.noteContent,
        userId: CREATOR_USER_ID,
      });
      console.log(`  NOTE ADDED: ${u.title}`);
      console.log(`    ${u.noteContent.length} chars`);
      results.updated.push({
        description: u.title,
        constraintId: u.constraintId,
        fieldsUpdated: u.fieldUpdates && Object.keys(u.fieldUpdates).length > 0
          ? Object.keys(u.fieldUpdates)
          : [],
      });
    } catch (noteErr) {
      console.log(`  FAILED: ${u.title} — ${noteErr.message}`);
      results.failed.push({
        description: u.title,
        error: noteErr.message,
      });
    }
  }

  // -----------------------------------------------------------------
  // Summary
  // -----------------------------------------------------------------
  console.log("\n\n=============================================================");
  console.log("  SYNC SUMMARY");
  console.log("=============================================================");
  console.log(`  Created:  ${results.created.length} new constraints`);
  console.log(`  Updated:  ${results.updated.length} existing constraints (notes + fields)`);
  console.log(`  Skipped:  ${results.skipped.length} (duplicate/dedup)`);
  console.log(`  Failed:   ${results.failed.length}`);
  console.log("=============================================================\n");

  if (results.created.length > 0) {
    console.log("NEW CONSTRAINTS CREATED:");
    for (const c of results.created) {
      console.log(`  [${c.priority.toUpperCase()}] ${c.constraintId}`);
      console.log(`    ${c.description}`);
    }
    console.log("");
  }

  if (results.updated.length > 0) {
    console.log("EXISTING CONSTRAINTS UPDATED:");
    for (const u of results.updated) {
      const fields = u.fieldsUpdated.length > 0
        ? ` (fields: ${u.fieldsUpdated.join(", ")})`
        : "";
      console.log(`  ${u.constraintId} — ${u.description}${fields}`);
    }
    console.log("");
  }

  if (results.skipped.length > 0) {
    console.log("SKIPPED:");
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

  // Output full results as JSON
  console.log("--- FULL RESULTS JSON ---");
  console.log(JSON.stringify(results, null, 2));
}

main().catch(err => {
  console.error("FATAL ERROR:", err);
  process.exit(1);
});
