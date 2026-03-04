#!/usr/bin/env node
/**
 * Blackford Solar Constraint Sync v2 — 2026-03-02
 *
 * UPGRADE from v1: Replaces keyword-overlap dedup with semantic smart-note-diff.
 * Instead of dropping notes that share keywords with existing same-day entries,
 * this version uses LLM-powered (or sentence-level fallback) comparison to extract
 * ONLY genuinely new information and push the delta.
 *
 * Approved proposal: push transcript-extracted constraints from the
 * Blackford Solar Constraints Meeting (2026-03-02) to ConstraintsPro.
 *
 * CROSS-REFERENCE ANALYSIS
 * ========================
 *
 * Meeting Constraint #1: "Off-Site Parking Shortage" (HIGH, LOGISTICS)
 *   -> MATCHES existing: k97f1k140edn8kn4xwa7rjbv1981yt77
 *      Already has a 3/2 note ("Sal will follow up on this spot") — minimal.
 *      The meeting transcript has MUCH more detail.
 *   -> ACTION: UPDATE — smart-diff note, update owner/description.
 *
 * Meeting Constraint #2: "Quality/Module Block Turnover" (MEDIUM, CONSTRUCTION)
 *   -> MATCHES existing: k97c95rxn1b376t2ebn6j2sh1981zt0p
 *      Already has a 3/2 note ("Trending in the right direction") — minimal.
 *   -> ACTION: UPDATE — smart-diff note, update owner.
 *
 * Meeting Constraint #3: "Trench Backfill Weather Delays" (MEDIUM, CONSTRUCTION)
 *   -> MATCHES existing: k979a0q8pkp3ybg0x742s3867d7zmt5z
 *      Already has a 3/2 note ("fully caught up on backfill for now") — minimal.
 *   -> ACTION: UPDATE — smart-diff note.
 *
 * Meeting Constraint #4: "Field IT Issues" (MEDIUM, CONSTRUCTION)
 *   -> MATCHES existing: k970w21s1cx7xb45c6k2bdrb3580h2ng
 *      Already has a 3/2 note ("Working on this this week") — minimal.
 *   -> ACTION: UPDATE — smart-diff note, update owner.
 *
 * Meeting Constraint #5: "QI Staffing Gap" (LOW, CONSTRUCTION)
 *   -> MATCHES existing: k97fb6vqkq5mfwv0evwtdc57z181qg33
 *      Already has a 3/2 note ("Will continue to monitor...") — minimal.
 *   -> ACTION: UPDATE — smart-diff note, update owner.
 *
 * RESULT: ALL 5 constraints from the meeting match existing records.
 *   - 0 NEW constraints to create
 *   - 5 EXISTING constraints to update (smart-diff notes from transcript)
 *   - 0 constraints to skip
 *
 * Source: 2026-03-02-blackford-constraints.txt (Recall.ai transcript)
 * Approval: User-approved sync proposal
 */

import { smartNoteDiff } from "./lib/smart-note-diff.mjs";

const CONVEX_URL = "https://charming-cuttlefish-923.convex.cloud";

// Blackford Solar project ID (from Convex)
const BLACKFORD_PROJECT_ID = "kh7bnx2gyw32rw1jcx7t5m831x7ytajh";

// Aaron Gonzalez DSC user (standard bot creator)
const CREATOR_USER_ID = "kn74p9jdq5zmns3vz172rvmpbn7yv694";

// Source metadata for notes
const SOURCE = "Blackford Solar Constraints Meeting — 2026-03-02";

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

// ===========================================================================
// 5 EXISTING CONSTRAINTS TO UPDATE (add detailed notes from transcript)
// ===========================================================================

const CONSTRAINT_UPDATES = [
  {
    constraintId: "k97f1k140edn8kn4xwa7rjbv1981yt77",
    title: "Off-Site Parking Shortage",
    fieldUpdates: {
      owner: "Salam Rawy",
      // Keep priority as high (already correct)
    },
    noteContent: `Constraint sync (${SOURCE}): Single biggest constraint — cannot add workforce without parking. Current lot candidate (Truck Parking Club, ~5mi out) contact unreachable. Copart sold the property, new owner unknown. Ross Hurlow's landowner liaison (LRE) says no farmland available across project footprint. Blackford Wind has no space either. IBEW contract requires graveled lot — grass-only is not viable per Scott Hunter. Scott cannot add module crews (currently 9K modules/day, needs 20 crews / ~200 workers). Konner Rodeffer notes structural ramping down in ~2 weeks will free some spots. Tim Cully investigating fairgrounds and OnX Maps for ownership tracing. Actions: Salam follow up Truck Parking Club lead; Ross/Gene find farmer willing to lease; Tim check fairgrounds. Josh Hauger has contract templates ready.`,
  },
  {
    constraintId: "k97c95rxn1b376t2ebn6j2sh1981zt0p",
    title: "Quality/Module Block Turnover",
    fieldUpdates: {
      owner: "James Nichols / Scott Hunter",
      // priority stays medium (was downgraded from high this call)
    },
    noteContent: `Constraint sync (${SOURCE}): Downgraded from HIGH to MEDIUM this call. Trend is positive per James Nichols — starting to get a couple blocks ahead. Bottleneck shifting from QA turnover to manpower (which is gated by parking). June deadline means fast turnover will be critical once crews ramp. Josh Hauger confirmed constraint is now Scott's manpower, not quality. Keep monitoring.`,
  },
  {
    constraintId: "k979a0q8pkp3ybg0x742s3867d7zmt5z",
    title: "Trench Backfill Weather Delays",
    fieldUpdates: {
      owner: "Scott Hunter / Timothy Cully",
    },
    noteContent: `Constraint sync (${SOURCE}): Backfill fully caught up through Block 10 as of today. Trying to finish Block 10 today for clean slate. 3+ inches rain forecasted this week — will cause flooded ditches and submerged cables. Scott will NOT open new trenches this week to avoid repeating Block 30 flooding situation. Ongoing battle through March. Tim confirmed fully backfilled up to 10.`,
  },
  {
    constraintId: "k970w21s1cx7xb45c6k2bdrb3580h2ng",
    title: "Field IT Issues",
    fieldUpdates: {
      owner: "Rebecca Mahar / Scott Hunter",
    },
    noteContent: `Constraint sync (${SOURCE}): Ongoing unresolved IT issues in the field — carryover item. Rebecca was at Delta Bobcat last week, could not address Blackford. She and Scott will meet this week to diagnose and resolve. No specific details on nature of IT problems discussed on this call.`,
  },
  {
    constraintId: "k97fb6vqkq5mfwv0evwtdc57z181qg33",
    title: "QI Staffing Gap",
    fieldUpdates: {
      owner: "Aaron Gonzalez / Salam Rawy",
      // priority stays low (downgraded this call)
    },
    noteContent: `Constraint sync (${SOURCE}): Originally needed 6 additional QIs. Using install crews as stopgap — downgraded to LOW. Aaron has candidates from other companies, sending to Bill Nichols today. Salam will reach out to other projects that may be slowing down for QI transfers. Scott warns QIs will be critical again once ramp-up hits 10+ module crews with June deadline. Consensus: solvable between PXs and PMs.`,
  },
];

// ===========================================================================
// MAIN EXECUTION
// ===========================================================================

async function main() {
  const results = { created: [], updated: [], skipped: [], failed: [] };

  console.log("=============================================================");
  console.log("  BLACKFORD SOLAR CONSTRAINT SYNC v2 — 2026-03-02");
  console.log("  Source: Constraints Meeting (Recall.ai transcript)");
  console.log("  Engine: smart-note-diff (semantic dedup)");
  console.log("=============================================================\n");

  // -----------------------------------------------------------------
  // Step 1: Verify existing constraints are still there
  // -----------------------------------------------------------------
  console.log("--- STEP 1: Verifying existing constraints ---");
  let existingConstraints;
  try {
    existingConstraints = await convexQuery("constraints:listByProject", {
      projectId: BLACKFORD_PROJECT_ID,
    });
    const open = existingConstraints.filter(c => c.status !== "resolved");
    console.log(`  Found ${existingConstraints.length} total (${open.length} open)\n`);
  } catch (e) {
    console.error("  FATAL: Could not fetch existing constraints:", e.message);
    process.exit(1);
  }

  // -----------------------------------------------------------------
  // Step 2: No new constraints to create (all 5 match existing)
  // -----------------------------------------------------------------
  console.log("--- STEP 2: No new constraints to create (all 5 match existing) ---\n");

  // -----------------------------------------------------------------
  // Step 3: UPDATE 5 existing constraints (smart-diff notes + fields)
  // -----------------------------------------------------------------
  console.log("--- STEP 3: Updating 5 existing constraints with smart-diff notes ---");
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

    // Apply field updates (owner, priority, etc.) ALWAYS — regardless of note dedup
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

    // Smart-diff the note against existing notes
    const deltaNoteContent = await smartNoteDiff(
      constraint.notes || "",
      u.noteContent,
      u.title,
    );

    if (deltaNoteContent === null) {
      // Truly nothing new — skip the note
      console.log(`  SMART_DIFF_SKIP: ${u.title} — no genuinely new information`);
      results.skipped.push({
        description: u.title,
        reason: "Smart diff: no genuinely new information vs existing same-day notes",
      });
      continue;
    }

    // Push the delta note (only new info)
    const noteToWrite = deltaNoteContent === u.noteContent
      ? u.noteContent  // Full note (no same-day notes existed)
      : `Constraint sync (${SOURCE}) [delta]: ${deltaNoteContent}`;

    try {
      await convexMutation("constraints:addNote", {
        constraintId: u.constraintId,
        content: noteToWrite,
        userId: CREATOR_USER_ID,
      });
      console.log(`  NOTE ADDED: ${u.title}`);
      console.log(`    Original: ${u.noteContent.length} chars`);
      console.log(`    Pushed:   ${noteToWrite.length} chars ${deltaNoteContent === u.noteContent ? "(full — no same-day conflict)" : "(delta — new info only)"}`);
      results.updated.push({
        description: u.title,
        constraintId: u.constraintId,
        noteType: deltaNoteContent === u.noteContent ? "full" : "delta",
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
  console.log("  SYNC SUMMARY (v2 — smart-note-diff)");
  console.log("=============================================================");
  console.log(`  Created:  ${results.created.length} new constraints`);
  console.log(`  Updated:  ${results.updated.length} existing constraints (notes + fields)`);
  console.log(`  Skipped:  ${results.skipped.length} (smart diff: nothing new)`);
  console.log(`  Failed:   ${results.failed.length}`);
  console.log("=============================================================\n");

  if (results.updated.length > 0) {
    console.log("EXISTING CONSTRAINTS UPDATED:");
    for (const u of results.updated) {
      console.log(`  ${u.constraintId} — ${u.description} [${u.noteType}]`);
    }
    console.log("");
  }

  if (results.skipped.length > 0) {
    console.log("SKIPPED (smart diff — nothing new):");
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
