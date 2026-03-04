#!/usr/bin/env node
/**
 * Pull all constraints + notes for Blackford and Three Rivers
 * for duplicate note cleanup analysis.
 */

const CONVEX_URL = "https://charming-cuttlefish-923.convex.cloud";
const BLACKFORD_PROJECT_ID = "kh7bnx2gyw32rw1jcx7t5m831x7ytajh";
const THREE_RIVERS_PROJECT_ID = "kh74kc7vdgteafhj7wy5d7zsvx7ytnvr";

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

async function pullProject(projectId, projectName) {
  const constraints = await convexQuery("constraints:listByProject", { projectId });
  const open = constraints.filter(c => c.status !== "resolved");

  console.log(`\n${"#".repeat(80)}`);
  console.log(`# ${projectName}: ${constraints.length} total, ${open.length} open`);
  console.log(`${"#".repeat(80)}`);

  for (const c of open) {
    console.log(`\n${"=".repeat(80)}`);
    console.log(`ID: ${c._id}`);
    console.log(`Description: ${c.description}`);
    console.log(`Status: ${c.status} | Priority: ${c.priority} | Owner: ${c.owner}`);
    const noteLines = (c.notes || "").split("\n").filter(l => l.trim());
    console.log(`Note count: ${noteLines.length}`);
    console.log("--- NOTES (each line is a separate note) ---");
    for (let i = 0; i < noteLines.length; i++) {
      console.log(`  [${i}] ${noteLines[i]}`);
    }
  }
}

async function main() {
  await pullProject(BLACKFORD_PROJECT_ID, "Blackford Solar");
  await pullProject(THREE_RIVERS_PROJECT_ID, "Three Rivers Solar");
}

main().catch(e => console.error("FATAL:", e));
