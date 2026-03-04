#!/usr/bin/env node
/**
 * Delta Bobcat Constraint Sync — 2026-03-03
 *
 * Pushes constraint updates from the 3/3 weekly constraints call to ConstraintsPro.
 * Source: 2026-03-03-delta-bobcat.txt (Recall.ai transcript) + user-provided notes
 *
 * CROSS-REFERENCE ANALYSIS
 * ========================
 *
 * C1: AG Elec Material - LBDs/Stringwire/Trunkbus Shoals
 *   -> MATCHES existing: k973ypkpzfzx61f84f9prm32zh7zm2pk (status: in_progress)
 *   -> Already has 3/3 note ("Only string wire and shoals trunk bus...") — minimal.
 *   -> ACTION: ADD NOTE — detailed update from call re: LBD contract, Adan/Cara, AWM dock.
 *
 * C2: MV sub potential impacts from LRDD ("MBA Sub")
 *   -> MATCHES existing: k9748es23ym7ak0907hv4rgtzh7zmwcx (status: in_progress)
 *   -> Already has 3/3 note ("Still on track") — minimal.
 *   -> ACTION: ADD NOTE — no change, 3/17 meeting still on.
 *
 * C3: Substation Readiness - JFE Underground MV Work at Risk
 *   -> MATCHES existing: k9726b6e2nh8x7jyp6g0cn2xrx81yh3z (status: open)
 *   -> User says change PENDING -> OPEN, but status is already "open" in ConstraintsPro.
 *   -> Already has 3/3 note ("Still setting up a meeting with JFE") — minimal.
 *   -> ACTION: ADD NOTE — Stephen at Nextera not concerned, coordination call being set up.
 *
 * C6+C7: Tariff Cost Increases — Shoals + GameChange (~$5M)
 *   -> MATCHES existing: k97a8v0k62pk3mp6pxnpfxkyvh81zycs (status: in_progress)
 *   -> Already combined from two constraints on 2/27.
 *   -> ACTION: ADD NOTE — Aaron consolidated 3 tariff items into 1. Mike's group handling.
 *
 * C8: Premier PV (Short-on pile caps)
 *   -> MATCHES existing: k97decdejk88rjxcnp9haaq4wn80yb62 (status: open)
 *   -> Already has 3/3 note ("Following up with Angel from premiere") — minimal.
 *   -> ACTION: ADD NOTE — Angel was sick + confused Delta Bobcat with Cyber Branch.
 *
 * PD10/BD10 Uptime:
 *   -> MATCHES existing: k972p2eakdfdafe9enxmgy88vn825gnh (status: resolved)
 *   -> Already resolved (confirmed wrong project — belongs to Coto).
 *   -> ACTION: CONFIRM — already handled. No action needed.
 *
 * RESULT: 5 constraints to update with notes, 1 already resolved (no action).
 */

const CONVEX_URL = "https://charming-cuttlefish-923.convex.cloud";

// Delta Bobcat project ID
const DELTA_BOBCAT_PROJECT_ID = "kh7b4wcje0hyn5368x7ragjj5s7ytby7";

// Aaron Gonzalez DSC user (standard bot creator)
const CREATOR_USER_ID = "kn74p9jdq5zmns3vz172rvmpbn7yv694";

// Source metadata
const SOURCE = "Delta Bobcat Weekly Constraints Call — 2026-03-03";

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
// CONSTRAINT UPDATES
// ===========================================================================

const CONSTRAINT_UPDATES = [
  {
    constraintId: "k973ypkpzfzx61f84f9prm32zh7zm2pk",
    title: "C1: AG Elec Material - LBDs/Stringwire/Trunkbus Shoals",
    noteContent: `Constraint sync (${SOURCE}): LBD contract believed fully executed (Randy signed). Adan confirming with Cara. String wire + trunk bus partially received, James reconciling. AWM cab material at unknown dock — James tracking dock ID + transit time.`,
  },
  {
    constraintId: "k9748es23ym7ak0907hv4rgtzh7zmwcx",
    title: "C2: MV sub potential impacts from LRDD (MBA Sub)",
    noteContent: `Constraint sync (${SOURCE}): No change, 3/17 meeting still on.`,
  },
  {
    constraintId: "k9726b6e2nh8x7jyp6g0cn2xrx81yh3z",
    title: "C3: Substation Readiness - JFE Underground MV Work at Risk",
    // Note: Status is already "open" — no change needed
    noteContent: `Constraint sync (${SOURCE}): Stephen at Nextera not overly concerned. Coordination call being set up now that Randy is back.`,
  },
  {
    constraintId: "k97a8v0k62pk3mp6pxnpfxkyvh81zycs",
    title: "C6+C7: Tariff Cost Increases — Shoals + GameChange (~$5M)",
    noteContent: `Constraint sync (${SOURCE}): Aaron consolidated 3 tariff items into 1. Mike's group handling, no site action needed.`,
  },
  {
    constraintId: "k97decdejk88rjxcnp9haaq4wn80yb62",
    title: "C8: Premier PV (Short-on pile caps)",
    noteContent: `Constraint sync (${SOURCE}): Angel at Premiere was sick AND confused Delta Bobcat with Cyber Branch. She's back, Ary pinging her now.`,
  },
];

// ===========================================================================
// MAIN EXECUTION
// ===========================================================================

async function main() {
  const results = { updated: [], skipped: [], failed: [], confirmed: [] };

  console.log("=============================================================");
  console.log("  DELTA BOBCAT CONSTRAINT SYNC — 2026-03-03");
  console.log("  Source: Weekly Constraints Call (Recall.ai transcript)");
  console.log("=============================================================\n");

  // -----------------------------------------------------------------
  // Step 1: Verify project exists
  // -----------------------------------------------------------------
  console.log("--- STEP 1: Verifying Delta Bobcat project ---");
  let existingConstraints;
  try {
    existingConstraints = await convexQuery("constraints:listByProject", {
      projectId: DELTA_BOBCAT_PROJECT_ID,
    });
    const open = existingConstraints.filter(c => c.status !== "resolved");
    console.log(`  Found ${existingConstraints.length} total (${open.length} open)\n`);
  } catch (e) {
    console.error("  FATAL: Could not fetch existing constraints:", e.message);
    process.exit(1);
  }

  // -----------------------------------------------------------------
  // Step 2: Confirm PD10/BD10 Uptime is resolved (wrong project)
  // -----------------------------------------------------------------
  console.log("--- STEP 2: Confirming PD10/BD10 Uptime removal ---");
  const pd10 = existingConstraints.find(c => c._id === "k972p2eakdfdafe9enxmgy88vn825gnh");
  if (pd10) {
    if (pd10.status === "resolved") {
      console.log(`  CONFIRMED: PD-10 Uptime is already resolved (status: ${pd10.status})`);
      console.log(`  This was correctly identified as belonging to Coto, not Delta Bobcat.`);
      results.confirmed.push({
        description: "PD-10 Uptime",
        constraintId: pd10._id,
        action: "Already resolved — wrong project (Coto)",
      });
    } else {
      console.log(`  WARNING: PD-10 Uptime is NOT resolved (status: ${pd10.status})`);
      console.log(`  Should be resolved — belongs to Coto, not Delta Bobcat.`);
    }
  } else {
    console.log(`  NOT FOUND: PD-10 Uptime constraint (may have been deleted)`);
  }
  console.log("");

  // -----------------------------------------------------------------
  // Step 3: Check C3 status (user says PENDING -> OPEN)
  // -----------------------------------------------------------------
  console.log("--- STEP 3: Checking C3 Substation Readiness status ---");
  const c3 = existingConstraints.find(c => c._id === "k9726b6e2nh8x7jyp6g0cn2xrx81yh3z");
  if (c3) {
    console.log(`  Current status: ${c3.status}`);
    if (c3.status === "open") {
      console.log(`  Status is already "open" — no change needed.`);
      console.log(`  (User requested PENDING -> OPEN; ConstraintsPro uses open/in_progress/resolved,`);
      console.log(`   not "pending". This constraint was created as "open" on 2/27.)`);
    } else {
      console.log(`  Status needs to change to "open".`);
      // This shouldn't happen based on our data, but handle it anyway
    }
  }
  console.log("");

  // -----------------------------------------------------------------
  // Step 4: UPDATE 5 existing constraints (add notes from call)
  // -----------------------------------------------------------------
  console.log("--- STEP 4: Adding notes to 5 existing constraints ---");
  for (const u of CONSTRAINT_UPDATES) {
    console.log(`\n  Processing: ${u.title}`);
    console.log(`  ${"─".repeat(60)}`);

    // Verify constraint exists
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

    console.log(`  Found: "${constraint.description}" (${constraint.status})`);

    // Add note
    try {
      await convexMutation("constraints:addNote", {
        constraintId: u.constraintId,
        content: u.noteContent,
        userId: CREATOR_USER_ID,
      });
      console.log(`  NOTE ADDED: ${u.title}`);
      console.log(`    Content: ${u.noteContent.substring(0, 100)}...`);
      results.updated.push({
        description: u.title,
        constraintId: u.constraintId,
        noteLength: u.noteContent.length,
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
  console.log("  SYNC SUMMARY — Delta Bobcat 2026-03-03");
  console.log("=============================================================");
  console.log(`  Updated:   ${results.updated.length} constraints (notes added)`);
  console.log(`  Confirmed: ${results.confirmed.length} (already handled)`);
  console.log(`  Skipped:   ${results.skipped.length}`);
  console.log(`  Failed:    ${results.failed.length}`);
  console.log("=============================================================\n");

  if (results.updated.length > 0) {
    console.log("CONSTRAINTS UPDATED:");
    for (const u of results.updated) {
      console.log(`  ${u.constraintId} — ${u.description} (${u.noteLength} chars)`);
    }
    console.log("");
  }

  if (results.confirmed.length > 0) {
    console.log("CONFIRMED (already handled):");
    for (const c of results.confirmed) {
      console.log(`  ${c.constraintId} — ${c.description}: ${c.action}`);
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
