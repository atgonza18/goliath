#!/usr/bin/env node
/**
 * Salt Branch Constraint Sync — 2026-03-02
 *
 * Approved proposal: push transcript-extracted constraints from the
 * Salt Branch Constraints Tracker call (2026-03-02 9:30 AM CST) to ConstraintsPro.
 *
 * Operations:
 *   CREATE (4 new constraints)
 *   UPDATE (8 existing constraints — add notes with latest intel)
 *
 * Source: 2026-03-02-salt-branch-constraints-tracker-readable.txt
 * Approval: User-approved sync proposal
 */

const CONVEX_URL = "https://charming-cuttlefish-923.convex.cloud";

// Salt Branch project ID (from Convex)
const SALT_BRANCH_PROJECT_ID = "kh70smhzz3dmq5zjqsck8dv5rs7yv5sy";

// Aaron Gonzalez DSC user (standard bot creator)
const CREATOR_USER_ID = "kn74p9jdq5zmns3vz172rvmpbn7yv694";

// Source metadata for notes
const SOURCE = "Salt Branch Constraints Tracker Call — 2026-03-02";

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

  // Parse notes (format: "M/D: content\nM/D: content")
  const lines = existingNotes.split("\n");
  const today = "3/2";  // Today's date in Convex note format

  // Find today's notes
  const todayNotes = lines.filter(line => line.startsWith(today + ":"));

  if (todayNotes.length === 0) return false;

  // Keyword overlap check (per constraints_manager dedup rules)
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
// 4 NEW CONSTRAINTS TO CREATE
// ===========================================================================

const NEW_CONSTRAINTS = [
  {
    description: "Weather Impact — Heavy rain this week. 0.8\" overnight, off-and-on Monday. Wednesday through Friday forecast to hit hard. Will limit pile driving, drilling, and outdoor operations. Team purchasing Milwaukee water pumps for slurry crew spotters to mitigate wet-hole conditions.",
    priority: "high",
    owner: "Site Team",
    category: "Other",  // Construction -> "Other" discipline in Convex
    status: "open",
    dueDate: null,
  },
  {
    description: "QI/Block Turnover Bottleneck — Blocks 1, 2, and 3 have racking complete but quality inspections are not finished, preventing turnover to module crews. 750+ workers on site but module crews cannot expand into available blocks. CRITICAL: throttling downstream production despite available runway. Eric to investigate with field engineers and get turnover status. Josh Hauger offered to send QIs/FEs from other projects if needed.",
    priority: "high",
    owner: "Eric Espinoza / QI Team",
    category: "Other",
    status: "open",
    dueDate: null,
  },
  {
    description: "Inverter Conduit/Grounding Sequencing — Inverter conduit and grounding installation was halted during remediation phase (pull testing failures required going back to fix/retest inverter piles). Now that inverter pull testing is passing, conduit/grounding work needs to restart and ramp up to keep circuits turning over. Ruben and team coordinating to resume sequenced installation across both SB1 and SB2.",
    priority: "medium",
    owner: "Ruben Mendoza",
    category: "Other",
    status: "open",
    dueDate: null,
  },
  {
    description: "Torque Wrench Shortage — All torque wrenches have been loaned out. Additional wrenches needed for incoming QIs and Gabe's racking crews. Ordering authorized by Shawn Pilney. Need to procure before additional QI/racking crews can be fully equipped.",
    priority: "medium",
    owner: "Gabriel Almodovar / Eric Espinoza",
    category: "Other",
    status: "open",
    dueDate: null,
  },
];

// ===========================================================================
// 8 EXISTING CONSTRAINTS TO UPDATE (add notes)
// ===========================================================================

const CONSTRAINT_UPDATES = [
  {
    constraintId: "k97cpnvh33tyf94qpdqjhzqxc98058wm",
    title: "Quality and Remed Manpower (Night Shift Remediation)",
    noteContent: `Constraint sync (${SOURCE}): Full night-shift remediation crew capability confirmed — will include driller, concrete capability (bag mix), redrives, and pull testing. Onboarding Thursday, nights starting Monday. 10 light plants ordered plus Milwaukee light towers for shaded areas. Night shift coordinator, CM, and quality all assigned. Headlamps ordered. 4 pull test crews and multiple 10-man remediation crews on day shift.`,
  },
  {
    constraintId: "k97bhsckb4karnpx05x6qx74xn7zmtjw",
    title: "Pile Installation / Drilling Rig Capacity",
    noteContent: `Constraint sync (${SOURCE}): Currently 9 rigs operating (6 HayDuck at 6.75", 3 Precision at 6.5"). Precision modifying equipment to accommodate 6.5" holes. 7" hole pull testing requires 5x the load vs 6.75" — not economically viable, staying with smaller bit. Piles moved up to Block 14 SB1 after catching up on south end. Weather (Wed-Fri) will impact drilling operations this week.`,
  },
  {
    constraintId: "k97f05a3je8h98z393a7x4hwx98138cr",
    title: "Sinking Piles / Kimley-Horn",
    noteContent: `Constraint sync (${SOURCE}): New sinking piles identified in Block 8, Section 1. Path forward: remove module, lift pile, collar with cast-in-place concrete. Kimley-Horn RFI still unanswered — they have not approved mix design and have not responded to pile location/plan submission. Eric to follow up with Stephen Lee at Kimley-Horn. Aaron flagged that it's "been in their court" too long. Action item logged to push KH for response.`,
  },
  {
    constraintId: "k9729j2j0hm6fb8pve4q53m84s81peen",
    title: "Module Staging / Trucking",
    noteContent: `Constraint sync (${SOURCE}): Now at 10 trucks total — 4 internal (split between piles/tubes/modules) + 6 third-party (modules only, onboarded last week). Third-party trucks in the group and working. Staging: working in Block 3 SB2, Block 19 SB1. Blocks 3, 2, 1 still need staging. Jessica managing staging for both Sanderfoot and internal crews.`,
  },
  {
    constraintId: "k970yaeywvy4fnqqvnf1eqhgm9825t2n",
    title: "AG Electrical Ramp-Up",
    noteContent: `Constraint sync (${SOURCE}): Materials pending — split loom, P-clips, zip ties all needed. Landon approved purchase last week, ETA unknown. Ruben requesting benchmark inspection to start work — willing to proceed without split loom and have spawn team fall back for installation when material arrives. 12 electricians releasing from Graceland (Rolando). Ruben and Andrew building full equipment/personnel list. Tachyon won't inspect wire management sections until all materials installed, but client can do benchmark. Not expected to affect MC — can be punch-listed.`,
  },
  {
    constraintId: "k977e4g3tvfvgh35g8sp10tz2h819ye9",
    title: "Apprentice Hours",
    noteContent: `Constraint sync (${SOURCE}): Trending positive — last week was just over 14%. Meeting tomorrow (Tuesday) to verify hitting requirements. No complaints from anyone. Keeping open until Tuesday review confirms numbers.`,
  },
  {
    constraintId: "k9720esn4y55g16hs511gpkpb58181gr",
    title: "Substation Conduit",
    noteContent: `Constraint sync (${SOURCE}): Shawn followed up with Dustin on Friday. Dustin has a 3-4 person crew still on-site. Doing several other pieces of work also. Targeting end of week to have everything wrapped up, weather permitting.`,
  },
  {
    constraintId: "k977qa6ky54efkkh5kxkhypge9824jka",
    title: "Parking",
    noteContent: `Constraint sync (${SOURCE}): 750 workers currently on site, heading to 800+. 40 new spots created recently but still constrained — original 7 acres insufficient. Van/carpool plan authorized: Zach's module crews (RV park campground, 10-15 min away) and Gabe's racking crews (200+ people using electrical yard, plus Micro 6 in Claremore) will organize carpooling with company vans. Same-crew grouping required so vans don't get stranded. Gas covered by supervisors.`,
  },
];

// ===========================================================================
// MAIN EXECUTION
// ===========================================================================

async function main() {
  const results = { created: [], updated: [], skipped: [], failed: [] };

  console.log("=============================================================");
  console.log("  SALT BRANCH CONSTRAINT SYNC — 2026-03-02");
  console.log("  Source: Constraints Tracker Call (9:30 AM CST)");
  console.log("=============================================================\n");

  // -----------------------------------------------------------------
  // Step 1: Fetch existing Salt Branch constraints for dedup
  // -----------------------------------------------------------------
  console.log("--- STEP 1: Fetching existing constraints for dedup ---");
  let existingConstraints;
  try {
    existingConstraints = await convexQuery("constraints:listByProject", {
      projectId: SALT_BRANCH_PROJECT_ID,
    });
    const open = existingConstraints.filter(c => c.status !== "resolved");
    console.log(`  Found ${existingConstraints.length} total (${open.length} open)\n`);
  } catch (e) {
    console.error("  FATAL: Could not fetch existing constraints:", e.message);
    process.exit(1);
  }

  // -----------------------------------------------------------------
  // Step 2: CREATE new constraints (with dedup check)
  // -----------------------------------------------------------------
  console.log("--- STEP 2: Creating 4 new constraints ---");
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
        projectId: SALT_BRANCH_PROJECT_ID,
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
          content: `Auto-synced from transcript — Source: ${SOURCE}`,
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
  // Step 3: UPDATE existing constraints (add notes, with dedup)
  // -----------------------------------------------------------------
  console.log("\n--- STEP 3: Updating 8 existing constraints with notes ---");
  for (const u of CONSTRAINT_UPDATES) {
    // Fetch the constraint to check for same-day note dedup
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

    // Same-day dedup check
    if (hasSameDayNote(constraint.notes, u.noteContent)) {
      console.log(`  DEDUP_SKIP: ${u.title} — same-day note already exists`);
      results.skipped.push({
        description: u.title,
        reason: "Same-day note already exists (dedup)",
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
      console.log(`  UPDATED: ${u.title}`);
      console.log(`    Note added (${u.noteContent.length} chars)\n`);
      results.updated.push({
        description: u.title,
        constraintId: u.constraintId,
      });
    } catch (noteErr) {
      console.log(`  FAILED: ${u.title} — ${noteErr.message}\n`);
      results.failed.push({
        description: u.title,
        error: noteErr.message,
      });
    }
  }

  // -----------------------------------------------------------------
  // Summary
  // -----------------------------------------------------------------
  console.log("\n=============================================================");
  console.log("  SYNC SUMMARY");
  console.log("=============================================================");
  console.log(`  Created:  ${results.created.length} new constraints`);
  console.log(`  Updated:  ${results.updated.length} existing constraints (notes added)`);
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
      console.log(`  ${u.constraintId} — ${u.description}`);
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
