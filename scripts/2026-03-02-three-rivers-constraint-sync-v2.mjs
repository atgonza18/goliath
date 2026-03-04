#!/usr/bin/env node
/**
 * Three Rivers Constraint Sync v2 — 2026-03-02
 *
 * UPGRADE from v1:
 *   1. Replaces keyword-overlap dedup with semantic smart-note-diff
 *   2. CRITICAL CHANGE: "IE Approval on Remediation Process" is FOLDED INTO the
 *      existing "Revised Pile Testing Requirements" constraint as a note, instead
 *      of being created as a new constraint. The IE approval IS part of the pile
 *      testing bottleneck — they belong together.
 *
 * Operations (v2):
 *   CREATE (2 new constraints — was 3, minus IE Approval which got folded)
 *     - Third-Party Racking Install Crews (NEW)
 *     - ABI Attachments for Pile Extraction (NEW)
 *   UPDATE (8 existing constraints — was 7, plus Revised Pile Testing gets IE note)
 *     - Revised Pile Testing Requirements (existing + IE Approval folded in)
 *     - First Circuit MC by 5/6 & In-Service by 6/29
 *     - Revised PD-10 Strategy
 *     - Module Delivery Staging — Off-Site Storage
 *     - Short on W6x9 Piles
 *     - Unforeseen Conditions Claim
 *     - Racking Install
 *     - Revised Pile Testing Requirements [IE Approval delta — force-pushed]
 *
 * Source: 2026-03-02-three-rivers-constraints.txt
 * Approval: User-approved sync proposal
 */

import { smartNoteDiff } from "./lib/smart-note-diff.mjs";

const CONVEX_URL = "https://charming-cuttlefish-923.convex.cloud";

// Three Rivers project ID (from Convex)
const THREE_RIVERS_PROJECT_ID = "kh74kc7vdgteafhj7wy5d7zsvx7ytnvr";

// Aaron Gonzalez DSC user (standard bot creator)
const CREATOR_USER_ID = "kn74p9jdq5zmns3vz172rvmpbn7yv694";

// Source metadata for notes
const SOURCE = "Three Rivers Constraints Meeting — 2026-03-02";

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
// Similarity check for dedup on new constraints (same as v1)
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
// 2 NEW CONSTRAINTS TO CREATE (was 3 — IE Approval folded into Pile Testing)
// ===========================================================================

const NEW_CONSTRAINTS = [
  {
    description: "Third-Party Racking Install Crews",
    priority: "high",
    owner: "Rob / Tanner",
    category: "Racking",
    status: "open",
    dueDate: null,
    noteContent: `Source: ${SOURCE}. Working with Rob on identifying good third-party talent for racking install. Had high-level conversations today — looks like a solid option given recruiting difficulties for pile install, racking, and electrical. Third-party crews are fully confident they can hit the 5/6 MC date for first circuit. Worst case if IE resolution takes all of March, crews ready to start April 1 and still confident they can complete on schedule. Weekly cadence calls (Mon/Wed/Fri) with these crews established. Action: Rob calling Swift Current SVP today. Send meeting invite series to Aaron for note-taking.`,
  },
  {
    description: "ABI Attachments for Pile Extraction",
    priority: "medium",
    owner: "Equipment Manager / Rodrigo",
    category: "Piles",
    status: "open",
    dueDate: null,
    noteContent: `Source: ${SOURCE}. Rodrigo raised that the on-site ABI attachment is not working and additional ABIs are needed for refusal pile extraction. Since these vibrating attachments take heavy wear from extracting piles, they will go down periodically — need at least 2 on hand. Worst case scenario: if IE does not approve embedment below 8.5 ft, piles at 6.5-8.5 ft that were tested per Kleinfelder/TRC requirements may need to be pulled entirely, requiring significant ABI work plus new pile orders. Equipment manager is sourcing additional ABIs to staff up the refusal remediation process.`,
  },
];

// ===========================================================================
// 8 EXISTING CONSTRAINTS TO UPDATE (7 original + Pile Testing gets IE note)
// ===========================================================================

const CONSTRAINT_UPDATES = [
  // ------------------------------------------------------------------
  // Original 7 constraint updates from v1
  // ------------------------------------------------------------------
  {
    constraintId: "k976nr140jwm0s3h1c6pw8kf6180zkh7",
    title: "Revised Pile Testing Requirements",
    noteContent: `Constraint sync (${SOURCE}): Project is on hold / right-hold pending owner response. Rob is calling Swift Current SVP (Philip) today to escalate. Tanner working on formal notice — may bundle into the unforeseen conditions claim or file separately. Team found email correspondence from Cameron asking about pile remediation status and refusals (Miranda located it). Action: Miranda to forward all correspondence to Boss for consolidation; Tanner/Boss to compile into SharePoint folder and share with Aaron/Josh. DSC team offered to run all meeting notes through AI model to build the formal case. Tyler expects Philip will push back on lack of formal notice, but client rep walked Block 3.8 and signed off knowing the situation. Need to dig through weeklies/monthlies back to November for documentation.`,
    // This note will be smart-diffed. If the v1 script already pushed it,
    // the diff engine will extract only new info (or skip if truly duplicate).
  },
  {
    constraintId: "k97c8gm6v8pxvg0gcxsmh5ybpn81vkhs",
    title: "First Circuit MC by 5/6 & In-Service by 6/29",
    noteContent: `Constraint sync (${SOURCE}): Tanner not personally worried about hitting first circuit MC by 5/6 — almost all piles in first circuit are already installed (not yet fully remediated/cut/punched/cleared for racking). From 5/6 MC to September for substantial completion on rest of project. First circuit in-service target is 6/29. Currently need ~700 piles/day but not working due to weather + IE hold. Will be closer to 1,000/day when actually ramping. Rodrigo confident at 100 piles per PD-10 (10 in production = 1,000/day potential). Snow is the biggest current impediment for mechanical teams — once gone, mud will slow slightly but efficiency improves significantly. Dropped to MEDIUM priority — good plan in place.`,
  },
  {
    constraintId: "k97cwfrhyt5qtz6bsy5j5307ns7zmmb8",
    title: "Revised PD-10 Strategy",
    noteContent: `Constraint sync (${SOURCE}): 10 PD-10s on site, 9 operators seated. Missing 1 operator for the 10th seat. Positions 8 and 9 were recently filled — one is a returning employee, the other transferred from rigor role with GF confidence and proficiency certs. Veronica looking for outsourced candidates to interview for the last seat. 4 additional PD-10s (making 14 total) still on site being serviced — equipment manager ensuring they are fully buttoned up before shipping to Clinton yard for inspection, then on to Tehuacana. Dropped to LOW.`,
  },
  {
    constraintId: "k971bjw07cy9s5xqjyx739r9ss81wnrj",
    title: "Module Delivery Staging — Off-Site Storage",
    noteContent: `Constraint sync (${SOURCE}): Warehouse is ~26,000 sq ft but need ~80,000 sq ft total. Large gravel lot surrounds the warehouse — Christine has sourced all materials needed to satisfy First Solar warranty requirements for outdoor storage (snow clearing, no swampy conditions, etc.). Cost is baked into the existing estimate. Next step: give the Jordans the green light and bake into existing contract. BLOCKER: Still waiting on client to set up call with First Solar to formally propose delivery address change. First Solar did not respond to Thursday/Friday call last week. Tanner sending email today proposing the off-site delivery plan. Urgency: site will not be ready to receive modules by March 16 delivery date. Meeting needed ASAP — today or tomorrow preferred.`,
  },
  {
    constraintId: "k973khz8b9gygcpvye96qx96xd81qrcx",
    title: "Short on W6x9 Piles",
    noteContent: `Constraint sync (${SOURCE}): Ze/Jorge update: received quote from Broken Government (supplier). Waiting to confirm the exact amount needed. RFI still pending on whether engineers approve using W6x8.5 instead of W6x9 — if approved, saves ~1,000 piles. The issue originated from the final pile plan coming with a modification (W6x9 instead of W6x8.5), but team had already procured W6x12 based on original BOM. Now need to replenish W6x12 stock. This is a TRC design miss — final BOM vs pile plans discrepancy.`,
  },
  {
    constraintId: "k976gkr6gfx315k858y38vpbq981yk6n",
    title: "Unforeseen Conditions Claim",
    noteContent: `Constraint sync (${SOURCE}): Tanner owes Tom Mayo a response. Considering whether to file one combined notice (unforeseen conditions bundling the design change delay) or two separate notices. The unforeseen conditions (excessive refusals) drove the need for the design change notice. First notice is primarily cost-driven — seeking compensation for increased pre-drill hole size, all re-drills due to refusals, and additional pile testing. Second notice (design change/delay) is more tactical — pressure to get an answer. Tyler expects owner will claim no formal notice was given, but team argues client rep walked Block 3.8 and signed off. Action: Tanner to call Tom Mayo to discuss strategy. Miranda found email from Cameron (Rob Turner) asking about pile remediation status. Team to compile all correspondence back to November.`,
  },
  {
    constraintId: "k97axe460nx9bhzkt04h0zy90n7zm88m",
    title: "Racking Install",
    noteContent: `Constraint sync (${SOURCE}): Racking is on hold pending IE approval on pile remediation/testing methodology. Third-party racking crews identified through Rob — fully confident they can hit 5/6 MC date. If IE resolution takes through end of March, crews staged to start April 1 and still make schedule. SCADA enclosure install relocation is ongoing and progressing well — may be moved off constraints list if no issues arise.`,
  },

  // ------------------------------------------------------------------
  // NEW in v2: IE Approval folded into Revised Pile Testing Requirements
  // The IE approval is fundamentally about the pile testing bottleneck —
  // the UL/IE hold is what's blocking pile remediation sign-off.
  // ------------------------------------------------------------------
  {
    constraintId: "k976nr140jwm0s3h1c6pw8kf6180zkh7",
    title: "Revised Pile Testing Requirements",
    _isIEFoldIn: true, // Flag for special handling in main loop
    noteContent: `IE Approval folded in (${SOURCE}): Waiting on Independent Engineer (IE/UL) to give comments back on remediation process before formal approval. Per Luke's update this morning: IE is going to hold up this process — project is functionally on hold pending IE green light. Weekly call cadence (Mon/Wed/Fri) established to stay on top of this. If IE takes all of March, racking crews staged to begin April 1. Rob calling Swift Current SVP today to escalate the UL/IE bottleneck. This is the gating item for the entire project.`,
  },
];

// ===========================================================================
// MAIN EXECUTION
// ===========================================================================

async function main() {
  const results = { created: [], updated: [], skipped: [], failed: [] };

  console.log("=============================================================");
  console.log("  THREE RIVERS CONSTRAINT SYNC v2 — 2026-03-02");
  console.log("  Source: Constraints Meeting Transcript");
  console.log("  Engine: smart-note-diff (semantic dedup)");
  console.log("  Changes: IE Approval folded into Pile Testing constraint");
  console.log("=============================================================\n");

  // -----------------------------------------------------------------
  // Step 1: Fetch existing Three Rivers constraints for dedup
  // -----------------------------------------------------------------
  console.log("--- STEP 1: Fetching existing constraints for dedup ---");
  let existingConstraints;
  try {
    existingConstraints = await convexQuery("constraints:listByProject", {
      projectId: THREE_RIVERS_PROJECT_ID,
    });
    const open = existingConstraints.filter(c => c.status !== "resolved");
    console.log(`  Found ${existingConstraints.length} total (${open.length} open)\n`);
  } catch (e) {
    console.error("  FATAL: Could not fetch existing constraints:", e.message);
    process.exit(1);
  }

  // -----------------------------------------------------------------
  // Step 2: CREATE 2 new constraints (was 3 — IE Approval folded in)
  // -----------------------------------------------------------------
  console.log("--- STEP 2: Creating 2 new constraints (IE Approval folded into Pile Testing) ---");
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
        projectId: THREE_RIVERS_PROJECT_ID,
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
      console.log(`  CREATED: ${c.description}`);
      console.log(`    -> ${constraintId}`);

      // Add detailed source note
      try {
        await convexMutation("constraints:addNote", {
          constraintId,
          content: c.noteContent,
          userId: CREATOR_USER_ID,
        });
        console.log(`    Note added.\n`);
      } catch (noteErr) {
        console.log(`    WARNING: Note failed: ${noteErr.message}\n`);
      }

      results.created.push({
        description: c.description,
        constraintId,
        priority: c.priority,
      });
    } catch (createErr) {
      console.log(`  FAILED: ${c.description}`);
      console.log(`    Error: ${createErr.message}\n`);
      results.failed.push({
        description: c.description,
        error: createErr.message,
      });
    }
  }

  // -----------------------------------------------------------------
  // Step 3: UPDATE 8 existing constraints (smart-diff notes)
  // -----------------------------------------------------------------
  console.log("\n--- STEP 3: Updating 8 existing constraints with smart-diff notes ---");
  console.log("  (includes IE Approval folded into Revised Pile Testing Requirements)\n");

  for (const u of CONSTRAINT_UPDATES) {
    const label = u._isIEFoldIn
      ? `${u.title} [IE FOLD-IN]`
      : u.title;

    console.log(`  Processing: ${label}`);
    console.log(`  ${"─".repeat(60)}`);

    // Fetch the constraint with its existing notes
    let constraint;
    try {
      constraint = await convexQuery("constraints:getWithNotes", {
        constraintId: u.constraintId,
      });
    } catch (e) {
      console.log(`  ERROR fetching ${label}: ${e.message}`);
      results.failed.push({
        description: label,
        error: `Fetch failed: ${e.message}`,
      });
      continue;
    }

    if (!constraint) {
      console.log(`  NOT FOUND: ${u.constraintId} (${label})`);
      results.failed.push({
        description: label,
        error: "Constraint not found",
      });
      continue;
    }

    // Smart-diff the note against existing notes
    const deltaNoteContent = await smartNoteDiff(
      constraint.notes || "",
      u.noteContent,
      label,
    );

    if (deltaNoteContent === null) {
      // Truly nothing new — skip
      console.log(`  SMART_DIFF_SKIP: ${label} — no genuinely new information`);
      results.skipped.push({
        description: label,
        reason: "Smart diff: no genuinely new information vs existing same-day notes",
      });
      continue;
    }

    // Determine note to write
    let noteToWrite;
    if (deltaNoteContent === u.noteContent) {
      // Full note — no same-day conflict
      noteToWrite = u.noteContent;
    } else if (u._isIEFoldIn) {
      // IE fold-in delta
      noteToWrite = `IE Approval folded in (${SOURCE}) [delta]: ${deltaNoteContent}`;
    } else {
      // Regular delta
      noteToWrite = `Constraint sync (${SOURCE}) [delta]: ${deltaNoteContent}`;
    }

    // Push the note
    try {
      await convexMutation("constraints:addNote", {
        constraintId: u.constraintId,
        content: noteToWrite,
        userId: CREATOR_USER_ID,
      });
      console.log(`  NOTE ADDED: ${label}`);
      console.log(`    Original: ${u.noteContent.length} chars`);
      console.log(`    Pushed:   ${noteToWrite.length} chars ${deltaNoteContent === u.noteContent ? "(full — no same-day conflict)" : "(delta — new info only)"}`);
      if (u._isIEFoldIn) {
        console.log(`    ** IE Approval content merged into Pile Testing constraint **`);
      }
      console.log("");
      results.updated.push({
        description: label,
        constraintId: u.constraintId,
        noteType: deltaNoteContent === u.noteContent ? "full" : "delta",
        isIEFoldIn: !!u._isIEFoldIn,
      });
    } catch (noteErr) {
      console.log(`  FAILED: ${label} — ${noteErr.message}\n`);
      results.failed.push({
        description: label,
        error: noteErr.message,
      });
    }
  }

  // -----------------------------------------------------------------
  // Summary
  // -----------------------------------------------------------------
  console.log("\n=============================================================");
  console.log("  SYNC SUMMARY (v2 — smart-note-diff)");
  console.log("=============================================================");
  console.log(`  Created:  ${results.created.length} new constraints (target: 2)`);
  console.log(`  Updated:  ${results.updated.length} existing constraints (target: 8)`);
  console.log(`  Skipped:  ${results.skipped.length} (smart diff: nothing new)`);
  console.log(`  Failed:   ${results.failed.length}`);
  console.log(`  IE Fold:  "IE Approval on Remediation Process" -> Pile Testing constraint`);
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
      const extra = u.isIEFoldIn ? " [IE FOLD-IN]" : "";
      console.log(`  ${u.constraintId} — ${u.description} [${u.noteType}]${extra}`);
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
