#!/usr/bin/env node
/**
 * Delete 13 duplicate constraints from ConstraintsPro.
 * These were created from Josh Hauger's Feb 27 email that echoed existing
 * constraint data back into the system.
 *
 * Uses the Convex HTTP API to call constraints:remove mutation.
 *
 * DO NOT DELETE these 2 (genuinely new Salt Branch constraints):
 * - k970h4temzwt234sby7t3zpvxd820ypr (Salt Branch - Remediation & block turnover)
 * - k9729gg5rb8swpahr0paxgefnx821xxz (Salt Branch - Pile installation rates)
 */

const CONVEX_URL = "https://charming-cuttlefish-923.convex.cloud";

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

// The 13 confirmed duplicate constraint IDs to delete
const DUPLICATES_TO_DELETE = [
  { id: "k977f6s2n1tp0gzdm3kcqs2ezx821ezf", label: "DUFF - exact batch dupe" },
  { id: "k971gh2td1s92g55p8yv0me25n820feh", label: "Tehuacana - exact batch dupe" },
  { id: "k970zr2pk2srh5pwq858dbnmed8200ev", label: "DUFF - dupe of J&B Contract Execution" },
  { id: "k978tvjkpwe81k06rhtthngqg58206h7", label: "DUFF - dupe of EDP Change Order" },
  { id: "k978ys1bqdrkm705h7gmegy6dh8208st", label: "DUFF - dupe of Equipment Recon" },
  { id: "k97858d4c2ar3v30qqx9rgyy7x82151j", label: "Mayes - dupe of Shoals Deliveries" },
  { id: "k971j4skvrzn0fey7wd5557ee1821q4s", label: "Scioto - dupe of Secondary power for substation" },
  { id: "k97dac6ncq4k4mzk8a19rmsj5x821v1y", label: "Union Ridge - dupe of Performance testing sub-contract" },
  { id: "k974ew0pa0fw7bak47yn72cqwx821ksb", label: "Tehuacana - dupe of PD10 Availability" },
  { id: "k977r7cbztygd5d7fcnsgaq49x820538", label: "Tehuacana - dupe of Shoals PO Execution" },
  { id: "k978gcbg68hr1552j4btccctsx820x20", label: "Pecan Praire - dupe of Initial CO to Repsol" },
  { id: "k97f1vqdvkfb224652sr4k2k1d8208zs", label: "Blackford Solar - dupe of parking/modules" },
  { id: "k97fe45c7edrxv687f6ekykn4h8219hb", label: "Blackford Solar - dupe of PPP turnover" },
];

// Safety check: these must NOT be deleted
const PROTECTED_IDS = [
  "k970h4temzwt234sby7t3zpvxd820ypr",  // Salt Branch - Remediation & block turnover
  "k9729gg5rb8swpahr0paxgefnx821xxz",  // Salt Branch - Pile installation rates
];

async function main() {
  console.log("=== DELETE DUPLICATE CONSTRAINTS ===");
  console.log(`Attempting to delete ${DUPLICATES_TO_DELETE.length} duplicate constraints.\n`);

  // Safety check
  for (const dup of DUPLICATES_TO_DELETE) {
    if (PROTECTED_IDS.includes(dup.id)) {
      console.error(`SAFETY ABORT: ${dup.id} is in the protected list! Aborting.`);
      process.exit(1);
    }
  }
  console.log("Safety check passed: no protected IDs in the deletion list.\n");

  // Step 1: Verify each constraint exists before deleting
  console.log("=== STEP 1: Verifying constraints exist ===");
  const verified = [];
  for (const dup of DUPLICATES_TO_DELETE) {
    try {
      const constraint = await convexQuery("constraints:getWithNotes", { constraintId: dup.id });
      if (constraint) {
        console.log(`  EXISTS: ${dup.id} — ${constraint.description?.substring(0, 60)}...`);
        verified.push({ ...dup, exists: true, description: constraint.description });
      } else {
        console.log(`  NOT FOUND: ${dup.id} (${dup.label}) — may have been already deleted`);
        verified.push({ ...dup, exists: false, description: null });
      }
    } catch (e) {
      console.log(`  ERROR checking ${dup.id}: ${e.message}`);
      verified.push({ ...dup, exists: false, description: null, error: e.message });
    }
  }

  const existCount = verified.filter(v => v.exists).length;
  console.log(`\n${existCount} of ${DUPLICATES_TO_DELETE.length} constraints verified to exist.\n`);

  // Step 2: Delete each verified constraint
  console.log("=== STEP 2: Deleting constraints ===");
  const results = [];
  for (const item of verified) {
    if (!item.exists) {
      results.push({ id: item.id, label: item.label, status: "skipped", reason: "not found / already deleted" });
      console.log(`  SKIP: ${item.id} (${item.label}) — not found`);
      continue;
    }

    try {
      await convexMutation("constraints:remove", { constraintId: item.id });
      results.push({ id: item.id, label: item.label, status: "deleted" });
      console.log(`  DELETED: ${item.id} (${item.label})`);
    } catch (e) {
      results.push({ id: item.id, label: item.label, status: "failed", error: e.message });
      console.log(`  FAILED: ${item.id} (${item.label}) — ${e.message}`);
    }
  }

  // Summary
  console.log("\n=== SUMMARY ===");
  const deleted = results.filter(r => r.status === "deleted").length;
  const skipped = results.filter(r => r.status === "skipped").length;
  const failed = results.filter(r => r.status === "failed").length;
  console.log(`Deleted: ${deleted} | Skipped: ${skipped} | Failed: ${failed}`);

  // Step 3: Verify Salt Branch constraints are still intact
  console.log("\n=== STEP 3: Verifying Salt Branch constraints are INTACT ===");
  for (const protectedId of PROTECTED_IDS) {
    try {
      const constraint = await convexQuery("constraints:getWithNotes", { constraintId: protectedId });
      if (constraint) {
        console.log(`  SAFE: ${protectedId} — "${constraint.description?.substring(0, 60)}..."`);
      } else {
        console.log(`  WARNING: ${protectedId} — NOT FOUND (may not exist yet)`);
      }
    } catch (e) {
      console.log(`  ERROR checking ${protectedId}: ${e.message}`);
    }
  }

  console.log("\n```json");
  console.log(JSON.stringify(results, null, 2));
  console.log("```");
}

main().catch(err => {
  console.error("FATAL ERROR:", err);
  process.exit(1);
});
