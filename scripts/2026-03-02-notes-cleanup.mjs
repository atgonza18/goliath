#!/usr/bin/env node
/**
 * Notes Cleanup Script — Blackford Solar & Three Rivers Solar
 * 2026-03-02
 *
 * Removes duplicate/redundant notes and cleans up meeting source headers.
 *
 * Approach:
 * - For each constraint, compute the cleaned notes string
 * - Call clearNotes to wipe the field
 * - Call addNote ONCE with the full cleaned string
 *   (addNote prepends "3/2: " to the content, so the first historical
 *   note line gets a harmless "3/2: " prefix. All subsequent lines are
 *   preserved exactly as-is, including their original dates.)
 * - Then verify the result
 */

const CONVEX_URL = "https://charming-cuttlefish-923.convex.cloud";
const CREATOR_USER_ID = "kn74p9jdq5zmns3vz172rvmpbn7yv694";

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
// For each constraint, define which note lines to delete and which to edit.
// Lines are 0-indexed from the notes field split by \n.
// ---------------------------------------------------------------------------

const CLEANUPS = [
  // =========================================================================
  // BLACKFORD SOLAR
  // =========================================================================
  {
    constraintId: "k979a0q8pkp3ybg0x742s3867d7zmt5z",
    title: "Backfill delays due to weather and site conditions",
    project: "Blackford",
    deleteIndices: [28], // "3/2: fully caught up on backfill for now."
    editMap: {
      29: "3/2: Backfill fully caught up through Block 10 as of today. Trying to finish Block 10 today for clean slate. 3+ inches rain forecasted this week — will cause flooded ditches and submerged cables. Scott will NOT open new trenches this week to avoid repeating Block 30 flooding situation. Ongoing battle through March. Tim confirmed fully backfilled up to 10.",
    },
  },
  {
    constraintId: "k970w21s1cx7xb45c6k2bdrb3580h2ng",
    title: "IT Hardware/Setup for HeavyJobs & Smart TagIT",
    project: "Blackford",
    deleteIndices: [8], // "3/2: Working on this this week."
    editMap: {
      9: "3/2: Ongoing unresolved IT issues in the field — carryover item. Rebecca was at Delta Bobcat last week, could not address Blackford. She and Scott will meet this week to diagnose and resolve. No specific details on nature of IT problems discussed on this call.",
    },
  },
  {
    constraintId: "k97fb6vqkq5mfwv0evwtdc57z181qg33",
    title: "QI Staffing Gap",
    project: "Blackford",
    deleteIndices: [1], // "3/2: Will continue to monitor..."
    editMap: {
      2: "3/2: Originally needed 6 additional QIs. Using install crews as stopgap — downgraded to LOW. Aaron has candidates from other companies, sending to Bill Nichols today. Salam will reach out to other projects that may be slowing down for QI transfers. Scott warns QIs will be critical again once ramp-up hits 10+ module crews with June deadline. Consensus: solvable between PXs and PMs.",
    },
  },
  {
    constraintId: "k97f1k140edn8kn4xwa7rjbv1981yt77",
    title: "Not enough parking to ramp up modules",
    project: "Blackford",
    deleteIndices: [1], // "3/2: Sal will follow up on this spot."
    editMap: {
      2: "3/2: Single biggest constraint — cannot add workforce without parking. Current lot candidate (Truck Parking Club, ~5mi out) contact unreachable. Copart sold the property, new owner unknown. Ross Hurlow's landowner liaison (LRE) says no farmland available across project footprint. Blackford Wind has no space either. IBEW contract requires graveled lot — grass-only is not viable per Scott Hunter. Scott cannot add module crews (currently 9K modules/day, needs 20 crews / ~200 workers). Konner Rodeffer notes structural ramping down in ~2 weeks will free some spots. Tim Cully investigating fairgrounds and OnX Maps for ownership tracing. Actions: Salam follow up Truck Parking Club lead; Ross/Gene find farmer willing to lease; Tim check fairgrounds. Josh Hauger has contract templates ready.",
    },
  },
  {
    constraintId: "k97c95rxn1b376t2ebn6j2sh1981zt0p",
    title: "Quality turnover to modules",
    project: "Blackford",
    deleteIndices: [1], // "3/2: Trending in the right direction."
    editMap: {
      2: "3/2: Downgraded from HIGH to MEDIUM this call. Trend is positive per James Nichols — starting to get a couple blocks ahead. Bottleneck shifting from QA turnover to manpower (which is gated by parking). June deadline means fast turnover will be critical once crews ramp. Josh Hauger confirmed constraint is now Scott's manpower, not quality. Keep monitoring.",
    },
  },

  // =========================================================================
  // THREE RIVERS SOLAR
  // =========================================================================
  {
    constraintId: "k97cwfrhyt5qtz6bsy5j5307ns7zmmb8",
    title: "Revised PD-10 Strategy",
    project: "Three Rivers",
    deleteIndices: [10], // "3/2: Just missing one seat. Currently interviewing."
    editMap: {
      11: "3/2: 10 PD-10s on site, 9 operators seated. Missing 1 operator for the 10th seat. Positions 8 and 9 were recently filled — one is a returning employee, the other transferred from rigor role with GF confidence and proficiency certs. Veronica looking for outsourced candidates to interview for the last seat. 4 additional PD-10s (making 14 total) still on site being serviced — equipment manager ensuring they are fully buttoned up before shipping to Clinton yard for inspection, then on to Tehuacana. Dropped to LOW.",
    },
  },
  {
    constraintId: "k97axe460nx9bhzkt04h0zy90n7zm88m",
    title: "Racking install",
    project: "Three Rivers",
    deleteIndices: [],
    editMap: {
      6: "3/2: Racking is on hold pending IE approval on pile remediation/testing methodology. Third-party racking crews identified through Rob — fully confident they can hit 5/6 MC date. If IE resolution takes through end of March, crews staged to start April 1 and still make schedule. SCADA enclosure install relocation is ongoing and progressing well — may be moved off constraints list if no issues arise.",
    },
  },
  {
    constraintId: "k976nr140jwm0s3h1c6pw8kf6180zkh7",
    title: "Revised Pile Testing Requirements",
    project: "Three Rivers",
    deleteIndices: [13, 14, 17], // [13]="Project on hold...", [14]="Additional ABI...", [17]=truncated duplicate of [16]
    editMap: {
      15: "3/2: Project is on hold / right-hold pending owner response. Rob is calling Swift Current SVP (Philip) today to escalate. Tanner working on formal notice — may bundle into the unforeseen conditions claim or file separately. Team found email correspondence from Cameron asking about pile remediation status and refusals (Miranda located it). Action: Miranda to forward all correspondence to Boss for consolidation; Tanner/Boss to compile into SharePoint folder and share with Aaron/Josh. DSC team offered to run all meeting notes through AI model to build the formal case. Tyler expects Philip will push back on lack of formal notice, but client rep walked Block 3.8 and signed off knowing the situation. Need to dig through weeklies/monthlies back to November for documentation.",
      16: "3/2: [IE Approval] Waiting on Independent Engineer (IE/UL) to give comments back on remediation process before formal approval. Per Luke's update: IE is going to hold up this process — project is functionally on hold pending IE green light. Weekly call cadence (Mon/Wed/Fri) established to stay on top of this. If IE takes all of March, racking crews staged to begin April 1. Rob calling Swift Current SVP today to escalate the UL/IE bottleneck. This is the gating item for the entire project.",
    },
  },
  {
    constraintId: "k973khz8b9gygcpvye96qx96xd81qrcx",
    title: "Short on W6x9 piles",
    project: "Three Rivers",
    deleteIndices: [3], // "3/2: Waiting on RFI response from engineers on size swapping."
    editMap: {
      4: "3/2: Ze/Jorge update — received quote from Broken Government (supplier). Waiting to confirm the exact amount needed. RFI still pending on whether engineers approve using W6x8.5 instead of W6x9 — if approved, saves ~1,000 piles. The issue originated from the final pile plan coming with a modification (W6x9 instead of W6x8.5), but team had already procured W6x12 based on original BOM. Now need to replenish W6x12 stock. This is a TRC design miss — final BOM vs pile plans discrepancy.",
    },
  },
  {
    constraintId: "k97c8gm6v8pxvg0gcxsmh5ybpn81vkhs",
    title: "First Circuit MC by 5/6 & In-Service by 6/29",
    project: "Three Rivers",
    deleteIndices: [1], // "3/2: Working with rob on identifying good talent..."
    editMap: {
      2: "3/2: Tanner not personally worried about hitting first circuit MC by 5/6 — almost all piles in first circuit are already installed (not yet fully remediated/cut/punched/cleared for racking). From 5/6 MC to September for substantial completion on rest of project. First circuit in-service target is 6/29. Currently need ~700 piles/day but not working due to weather + IE hold. Will be closer to 1,000/day when actually ramping. Rodrigo confident at 100 piles per PD-10 (10 in production = 1,000/day potential). Snow is the biggest current impediment for mechanical teams — once gone, mud will slow slightly but efficiency improves significantly. Dropped to MEDIUM priority — good plan in place.",
    },
  },
  {
    constraintId: "k971bjw07cy9s5xqjyx739r9ss81wnrj",
    title: "Module Delivery Staging",
    project: "Three Rivers",
    deleteIndices: [4, 5], // [4]="Making sure we can satisfy...", [5]="Shooting to get a call..."
    editMap: {
      6: "3/2: Warehouse is ~26,000 sq ft but need ~80,000 sq ft total. Large gravel lot surrounds the warehouse — Christine has sourced all materials needed to satisfy First Solar warranty requirements for outdoor storage (snow clearing, no swampy conditions, etc.). Cost is baked into the existing estimate. Next step: give the Jordans the green light and bake into existing contract. BLOCKER: Still waiting on client to set up call with First Solar to formally propose delivery address change. First Solar did not respond to Thursday/Friday call last week. Tanner sending email today proposing the off-site delivery plan. Urgency: site will not be ready to receive modules by March 16 delivery date. Meeting needed ASAP — today or tomorrow preferred.",
    },
  },
  {
    constraintId: "k976gkr6gfx315k858y38vpbq981yk6n",
    title: "Unforeseen Conditions Claim",
    project: "Three Rivers",
    deleteIndices: [],
    editMap: {
      1: "3/2: Tanner owes Tom Mayo a response. Considering whether to file one combined notice (unforeseen conditions bundling the design change delay) or two separate notices. The unforeseen conditions (excessive refusals) drove the need for the design change notice. First notice is primarily cost-driven — seeking compensation for increased pre-drill hole size, all re-drills due to refusals, and additional pile testing. Second notice (design change/delay) is more tactical — pressure to get an answer. Tyler expects owner will claim no formal notice was given, but team argues client rep walked Block 3.8 and signed off. Action: Tanner to call Tom Mayo to discuss strategy. Miranda found email from Cameron (Rob Turner) asking about pile remediation status. Team to compile all correspondence back to November.",
    },
  },
  {
    constraintId: "k97c7xp7r37bx8kcka6brkzbg1825m5n",
    title: "ABI Attachments for Pile Extraction",
    project: "Three Rivers",
    deleteIndices: [],
    editMap: {
      0: "3/2: Rodrigo raised that the on-site ABI attachment is not working and additional ABIs are needed for refusal pile extraction. Since these vibrating attachments take heavy wear from extracting piles, they will go down periodically — need at least 2 on hand. Worst case scenario: if IE does not approve embedment below 8.5 ft, piles at 6.5-8.5 ft that were tested per Kleinfelder/TRC requirements may need to be pulled entirely, requiring significant ABI work plus new pile orders. Equipment manager is sourcing additional ABIs to staff up the refusal remediation process.",
    },
  },
];

// ---------------------------------------------------------------------------
// Execute cleanup
// ---------------------------------------------------------------------------

async function main() {
  console.log("=============================================================");
  console.log("  NOTES CLEANUP — Blackford & Three Rivers");
  console.log("  Date: 2026-03-02");
  console.log("=============================================================\n");

  const results = { success: [], failed: [] };

  for (const cleanup of CLEANUPS) {
    console.log(`\n${"=".repeat(70)}`);
    console.log(`[${cleanup.project}] ${cleanup.title}`);
    console.log(`  ID: ${cleanup.constraintId}`);

    // 1. Fetch current constraint
    let constraint;
    try {
      constraint = await convexQuery("constraints:getWithNotes", {
        constraintId: cleanup.constraintId,
      });
    } catch (e) {
      console.log(`  ERROR fetching: ${e.message}`);
      results.failed.push({ ...cleanup, error: e.message });
      continue;
    }

    if (!constraint) {
      console.log(`  NOT FOUND`);
      results.failed.push({ ...cleanup, error: "Constraint not found" });
      continue;
    }

    const currentNotes = constraint.notes || "";
    const noteLines = currentNotes.split("\n");
    console.log(`  Current lines: ${noteLines.length}`);

    // 2. Build cleaned notes
    const deleteSet = new Set(cleanup.deleteIndices);
    const newLines = [];
    let deletedCount = 0;
    let editedCount = 0;

    for (let i = 0; i < noteLines.length; i++) {
      if (deleteSet.has(i)) {
        console.log(`  DELETE [${i}]: "${noteLines[i].substring(0, 70)}..."`);
        deletedCount++;
        continue;
      }
      if (cleanup.editMap[i] !== undefined) {
        console.log(`  EDIT [${i}]:`);
        console.log(`    FROM: "${noteLines[i].substring(0, 70)}..."`);
        console.log(`    TO:   "${cleanup.editMap[i].substring(0, 70)}..."`);
        newLines.push(cleanup.editMap[i]);
        editedCount++;
      } else {
        newLines.push(noteLines[i]);
      }
    }

    const cleanedNotes = newLines.join("\n");
    console.log(`  Result: ${noteLines.length} -> ${newLines.length} lines (deleted ${deletedCount}, edited ${editedCount})`);

    if (cleanedNotes === currentNotes) {
      console.log(`  NO CHANGE NEEDED — skipping`);
      continue;
    }

    // 3. Clear notes
    console.log(`  Clearing notes...`);
    try {
      await convexMutation("constraints:clearNotes", {
        constraintId: cleanup.constraintId,
        userId: CREATOR_USER_ID,
      });
      console.log(`  Notes cleared.`);
    } catch (e) {
      console.log(`  ERROR clearing notes: ${e.message}`);
      results.failed.push({ ...cleanup, error: `Clear failed: ${e.message}` });
      continue;
    }

    // 4. Add back the cleaned notes as a single addNote call
    // addNote prepends "3/2: " to the content, so we need to account for that.
    // The first line of cleanedNotes might already start with a date prefix.
    // To avoid "3/2: 1/6: ..." we strip the first line's date prefix if present
    // and let addNote's "3/2:" replace it. BUT this only works if the first line
    // was originally dated 3/2. For historical lines, this changes the date.
    //
    // APPROACH: We split the cleaned notes into two parts:
    //   - historicalLines: all lines that DON'T start with "3/2:"
    //   - march2Lines: all lines that start with "3/2:"
    //
    // We first add the historical lines as one big addNote (accepting 3/2: prefix on line 1).
    // Then we add each march2 line individually (stripping the "3/2:" prefix since addNote adds it).
    //
    // Actually, the simplest approach: the ENTIRE cleaned notes string goes as
    // the content of ONE addNote call. The first line gets "3/2: " prepended.
    // For constraints where the first line has no date (like "Need to determine..."),
    // this actually ADDS a proper date prefix. For constraints where the first line
    // has a date (like "1/6: still delay..."), it becomes "3/2: 1/6: still delay..."
    // which is slightly redundant but preserves all data.
    //
    // BETTER: For the second case, strip the first line's "M/D: " prefix since
    // addNote will add today's "3/2: " prefix. This loses the original date on
    // line 1, which is NOT acceptable.
    //
    // BEST: Accept the "3/2: " prefix on line 1. It's cosmetic and will be pushed
    // down by future notes. The important thing is data integrity.

    console.log(`  Adding cleaned notes back...`);
    try {
      await convexMutation("constraints:addNote", {
        constraintId: cleanup.constraintId,
        content: cleanedNotes,
        userId: CREATOR_USER_ID,
      });
      console.log(`  SUCCESS — notes restored with ${newLines.length} lines`);
      results.success.push({
        constraintId: cleanup.constraintId,
        project: cleanup.project,
        title: cleanup.title,
        linesDeleted: deletedCount,
        linesEdited: editedCount,
        linesBefore: noteLines.length,
        linesAfter: newLines.length,
      });
    } catch (e) {
      console.log(`  ERROR adding notes back: ${e.message}`);
      console.log(`  WARNING: Notes were cleared but could not be restored!`);
      console.log(`  Cleaned notes string saved below for manual recovery:`);
      console.log(`  ---BEGIN---`);
      console.log(cleanedNotes);
      console.log(`  ---END---`);
      results.failed.push({ ...cleanup, error: `AddNote failed after clear: ${e.message}`, cleanedNotes });
    }

    // Small delay to avoid rate limiting
    await new Promise(r => setTimeout(r, 200));
  }

  // Summary
  console.log(`\n\n${"=".repeat(70)}`);
  console.log("  CLEANUP SUMMARY");
  console.log("=".repeat(70));
  console.log(`  Success: ${results.success.length}`);
  console.log(`  Failed:  ${results.failed.length}`);

  if (results.success.length > 0) {
    console.log("\n  SUCCESSFUL CLEANUPS:");
    for (const s of results.success) {
      console.log(`    [${s.project}] ${s.title}: ${s.linesBefore} -> ${s.linesAfter} lines (deleted ${s.linesDeleted}, edited ${s.linesEdited})`);
    }
  }

  if (results.failed.length > 0) {
    console.log("\n  FAILED:");
    for (const f of results.failed) {
      console.log(`    [${f.project}] ${f.title}: ${f.error}`);
    }
  }

  console.log(`\n${"=".repeat(70)}\n`);

  // Verify results
  console.log("VERIFICATION — Re-reading cleaned constraints:\n");
  for (const s of results.success) {
    try {
      const c = await convexQuery("constraints:getWithNotes", { constraintId: s.constraintId });
      const lines = (c.notes || "").split("\n").filter(l => l.trim());
      console.log(`[${s.project}] ${s.title}: ${lines.length} note lines`);
      // Show last 3 lines
      const showLines = lines.slice(-3);
      for (const line of showLines) {
        console.log(`  ${line.substring(0, 120)}${line.length > 120 ? "..." : ""}`);
      }
      console.log("");
    } catch (e) {
      console.log(`  Error verifying ${s.constraintId}: ${e.message}`);
    }
  }
}

main().catch(err => {
  console.error("FATAL:", err);
  process.exit(1);
});
