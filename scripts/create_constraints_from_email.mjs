#!/usr/bin/env node
/**
 * DEPRECATED (2026-02-28): This one-off script was used to manually create
 * constraints in ConstraintsPro from Josh Hauger's DSC email data before the
 * automated pipeline was built. It should NOT be used going forward.
 *
 * The Goliath email pipeline now handles Hauger emails automatically:
 *   - Constraint content -> matched to existing constraints, appended as notes
 *   - Production content -> stored as intel in MemoryStore + project files
 *   - New constraints are NEVER auto-created from Hauger DSC summaries
 *
 * See: telegram-bot/bot/services/constraint_logger.py (process_hauger_email)
 *      telegram-bot/bot/services/email_poller.py (_classify_email: "hauger_update")
 *
 * Original purpose: Create constraints in ConstraintsPro from parsed email data.
 * Calls the Convex HTTP API directly.
 */

const CONVEX_URL = "https://charming-cuttlefish-923.convex.cloud";

async function convexQuery(path, args = {}) {
  // Strip undefined values
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

// Map category to discipline
function categoryToDiscipline(category) {
  const map = {
    "PROCUREMENT": "Procurement",
    "CONSTRUCTION": "Other",  // No exact match, use Other
    "ENGINEERING": "Other",
    "OTHER": "Other",
  };
  return map[category] || "Other";
}

// Check if two descriptions are about the same constraint
function isSimilar(desc1, desc2) {
  const normalize = s => s.toLowerCase().replace(/[^a-z0-9\s]/g, '').trim();
  const n1 = normalize(desc1);
  const n2 = normalize(desc2);

  // Extract key phrases (words > 3 chars)
  const words1 = new Set(n1.split(/\s+/).filter(w => w.length > 3));
  const words2 = new Set(n2.split(/\s+/).filter(w => w.length > 3));

  // Count overlap
  let overlap = 0;
  for (const w of words1) {
    if (words2.has(w)) overlap++;
  }

  // If more than 50% of key words overlap, consider similar
  const minLen = Math.min(words1.size, words2.size);
  if (minLen === 0) return false;
  const overlapRatio = overlap / minLen;

  // Also check if one description contains key phrases from the other
  const keyPhrases1 = extractKeyPhrases(desc1);
  const keyPhrases2 = extractKeyPhrases(desc2);
  let phraseOverlap = 0;
  for (const p of keyPhrases1) {
    if (n2.includes(p.toLowerCase())) phraseOverlap++;
  }

  return overlapRatio > 0.5 || (phraseOverlap >= 2 && overlapRatio > 0.3);
}

function extractKeyPhrases(desc) {
  const phrases = [];
  // Extract specific identifiable terms
  const patterns = [
    /PD-10/gi, /GPS/gi, /Shoals/gi, /CAB/gi, /DMC/gi, /PPP/gi,
    /pile\s+(?:installation|testing|production|remediation)/gi,
    /racking/gi, /module\s+production/gi, /manpower/gi,
    /string\s+wire/gi, /substation/gi, /T-Line/gi,
    /schedule\s+(?:risk|relief)/gi, /acceleration/gi,
    /remediation/gi, /ground\s+conditions/gi,
    /performance\s+testing/gi, /SEC\s+contract/gi,
    /B&E\s+contract/gi, /equipment\s+overages/gi,
    /reveal\s+heights/gi, /tolerances/gi,
    /messenger\s+wire/gi, /drill\s+rigs/gi,
    /parking/gi, /landowner/gi,
    /load\s+test/gi, /circuit\s+MC/gi,
    /COD/gi, /Keeley/gi, /RDO/gi,
  ];
  for (const p of patterns) {
    const matches = desc.match(p);
    if (matches) phrases.push(...matches);
  }
  return phrases;
}

const CONSTRAINTS_TO_CREATE = [
  {
    description: "Performance testing contract not in place, putting SC at risk. SEC contract approved in CRT, pending execution.",
    project_key: "union-ridge",
    project_name: "Union Ridge",
    priority: "HIGH",
    owner: "SEC / MasTec Contracts",
    need_by_date: null,
    category: "PROCUREMENT"
  },
  {
    description: "Latest schedule to owner is still pushing SC months with no approved schedule relief. B&E contract for 3rd party schedule review submitted to CRT for approval.",
    project_key: "duff",
    project_name: "Duff",
    priority: "HIGH",
    owner: "DSC Team / EDP",
    need_by_date: null,
    category: "OTHER"
  },
  {
    description: "Significant risk in achieving current schedule at current production rates. Revised schedule sent to EDP — SC pushed an additional month to mid-August (6-month total push to baseline).",
    project_key: "duff",
    project_name: "Duff",
    priority: "HIGH",
    owner: "Site Team / EDP",
    need_by_date: null,
    category: "CONSTRUCTION"
  },
  {
    description: "~6,000 piles identified with reveal heights out of tolerances. Team awaiting RFI response on next steps.",
    project_key: "duff",
    project_name: "Duff",
    priority: "HIGH",
    owner: "EOR",
    need_by_date: null,
    category: "ENGINEERING"
  },
  {
    description: "Equipment overages on site pose significant cost risk. Initial review resulted in call-off of ~$100K/month savings.",
    project_key: "duff",
    project_name: "Duff",
    priority: "MEDIUM",
    owner: "Site Team",
    need_by_date: null,
    category: "CONSTRUCTION"
  },
  {
    description: "Remediation & block turnover to racking poses significant schedule risk. Racking still being impacted by remediation; team exploring additional 3rd party resources.",
    project_key: "salt-branch",
    project_name: "Salt Branch",
    priority: "HIGH",
    owner: "Site Team",
    need_by_date: null,
    category: "CONSTRUCTION"
  },
  {
    description: "Pile installation rates impacted by drilling runway. Team will have 15 drill rigs onsite by Saturday.",
    project_key: "salt-branch",
    project_name: "Salt Branch",
    priority: "HIGH",
    owner: "Site Team / Equipment Team",
    need_by_date: null,
    category: "CONSTRUCTION"
  },
  {
    description: "T-Line pole conflict with Blackford Wind MV line. Keeley to re-mob 3/4.",
    project_key: "blackford",
    project_name: "Blackford",
    priority: "HIGH",
    owner: "Keeley",
    need_by_date: "2026-03-04",
    category: "CONSTRUCTION"
  },
  {
    description: "Pile installation on hold due to PPP delays. Delayed 7 days awaiting EOR response; team has escalated — ETA 3/2.",
    project_key: "blackford",
    project_name: "Blackford",
    priority: "HIGH",
    owner: "EOR",
    need_by_date: "2026-03-02",
    category: "ENGINEERING"
  },
  {
    description: "Module production impacted by manpower parking availability. Ideal space identified; team coordinating with landowner on cost.",
    project_key: "blackford",
    project_name: "Blackford",
    priority: "MEDIUM",
    owner: "Site Team / Landowner",
    need_by_date: null,
    category: "CONSTRUCTION"
  },
  {
    description: "Pile testing requirements recently increased, impacting remediation. Pile remediation and racking on hold awaiting SCE to finalize load test documentation.",
    project_key: "three-rivers",
    project_name: "Three Rivers",
    priority: "HIGH",
    owner: "SCE",
    need_by_date: null,
    category: "ENGINEERING"
  },
  {
    description: "First Circuit MC of 5/6 at risk due to pile installation rates. Site coordinating with equipment team to confirm additional 3 PD-10s.",
    project_key: "three-rivers",
    project_name: "Three Rivers",
    priority: "HIGH",
    owner: "Equipment Team",
    need_by_date: "2026-05-06",
    category: "CONSTRUCTION"
  },
  {
    description: "Shoals delivery schedule will prevent achieving accelerated LRE May COD commitment. Procurement working with Shoals to pull delivery into May.",
    project_key: "mayes",
    project_name: "Mayes",
    priority: "HIGH",
    owner: "Procurement / Shoals",
    need_by_date: "2026-05-31",
    category: "PROCUREMENT"
  },
  {
    description: "PD-10 uptime challenges impacting pile production. 11 of 16 machines in operation; constrained by operator/rigger manpower and DC workflow conflicts.",
    project_key: "mayes",
    project_name: "Mayes",
    priority: "HIGH",
    owner: "Equipment Team / Site Team",
    need_by_date: null,
    category: "CONSTRUCTION"
  },
  {
    description: "Ground conditions significantly impacting civil, electrical, and pile production. PV grading continues; 250 piles installed this week; MV starting 3/9.",
    project_key: "scioto-ridge",
    project_name: "Scioto Ridge",
    priority: "HIGH",
    owner: "Site Team",
    need_by_date: null,
    category: "CONSTRUCTION"
  },
  {
    description: "Substation subcontractor missing DMC connectors — long lead procurement risk. Team coordinating with EOR and procurement on next steps.",
    project_key: "scioto-ridge",
    project_name: "Scioto Ridge",
    priority: "HIGH",
    owner: "EOR / Procurement",
    need_by_date: null,
    category: "PROCUREMENT"
  },
  {
    description: "String wire required to complete project was supposed to be delivered 1/23 and never arrived. Replacement wire has 6-8 week lead time; team coordinating with engineering and logistics to source alternative — potential warranty risk.",
    project_key: "graceland",
    project_name: "Graceland",
    priority: "HIGH",
    owner: "Engineering / Logistics",
    need_by_date: "2026-03-15",
    category: "PROCUREMENT"
  },
  {
    description: "PD-10 GPS equipment missing from rental units, impacting scheduled pile production start on 3/6. RDO will be onsite next week to install Carlson GPS equipment on 6 rental units.",
    project_key: "tehuacana",
    project_name: "Tehuacana",
    priority: "HIGH",
    owner: "RDO",
    need_by_date: "2026-03-06",
    category: "CONSTRUCTION"
  },
  {
    description: "PD-10 fleet availability poses risk to required pile production rates. Site coordinating with equipment team to procure additional units.",
    project_key: "tehuacana",
    project_name: "Tehuacana",
    priority: "MEDIUM",
    owner: "Equipment Team",
    need_by_date: null,
    category: "CONSTRUCTION"
  },
  {
    description: "Shoals material (CAB & messenger wire) was not ordered, posing significant schedule risk. PO executed this week; awaiting final delivery schedule from Shoals.",
    project_key: "tehuacana",
    project_name: "Tehuacana",
    priority: "HIGH",
    owner: "Procurement / Shoals",
    need_by_date: null,
    category: "PROCUREMENT"
  },
  {
    description: "Acceleration CO poses significant upfront cost risk if not resolved. Owner agreed to non-accelerated schedule + $2.4M; finalizing redlines this week.",
    project_key: "pecan-prairie",
    project_name: "Pecan Prairie",
    priority: "HIGH",
    owner: "Owner / MasTec Commercial",
    need_by_date: null,
    category: "OTHER"
  },
  {
    description: "Changes to substation design pose long-lead procurement risk. Delivery updated from September to November; procurement exploring alternative sourcing options.",
    project_key: "pecan-prairie",
    project_name: "Pecan Prairie",
    priority: "HIGH",
    owner: "Procurement",
    need_by_date: "2026-11-01",
    category: "PROCUREMENT"
  }
];

async function main() {
  const results = [];

  // Step 1: Get all projects
  console.log("=== STEP 1: Fetching projects ===");
  const projects = await convexQuery("projects:list", {});
  console.log(`Found ${projects.length} projects:`);

  // Build project name-to-ID lookup (normalize names)
  const projectMap = {};
  for (const p of projects) {
    projectMap[p.name.toLowerCase()] = p._id;
    if (p.code) projectMap[p.code.toLowerCase()] = p._id;
    console.log(`  ${p.name} (code: ${p.code || 'none'}) -> ${p._id}`);
  }

  // Step 1b: Get a DSC user to use as the creator
  console.log("\n=== Getting DSC users ===");
  const dscUsers = await convexQuery("users:listDscUsers", {});
  if (!dscUsers || dscUsers.length === 0) {
    console.error("ERROR: No DSC users found!");
    process.exit(1);
  }
  const creatorUserId = dscUsers[0]._id;
  console.log(`Using creator: ${dscUsers[0].name || dscUsers[0].email} (${creatorUserId})`);

  // Step 2: For each unique project, fetch existing constraints
  const uniqueProjects = [...new Set(CONSTRAINTS_TO_CREATE.map(c => c.project_name))];
  const existingByProject = {};

  console.log("\n=== STEP 2: Fetching existing constraints for duplicate check ===");
  for (const projName of uniqueProjects) {
    const projId = projectMap[projName.toLowerCase()];
    if (!projId) {
      console.log(`  WARNING: No project ID found for "${projName}"`);
      existingByProject[projName] = [];
      continue;
    }
    try {
      const existing = await convexQuery("constraints:listByProject", { projectId: projId });
      existingByProject[projName] = existing || [];
      console.log(`  ${projName}: ${existingByProject[projName].length} existing constraints`);
    } catch (e) {
      console.log(`  ${projName}: ERROR fetching - ${e.message}`);
      existingByProject[projName] = [];
    }
  }

  // Step 3 & 4: Create constraints and add notes
  console.log("\n=== STEP 3: Creating constraints ===");
  for (const c of CONSTRAINTS_TO_CREATE) {
    const projId = projectMap[c.project_name.toLowerCase()];
    if (!projId) {
      console.log(`  SKIP (no project): ${c.project_name} — ${c.description.substring(0, 60)}...`);
      results.push({
        description: c.description,
        project_name: c.project_name,
        priority: c.priority,
        status: "failed",
        constraint_id: null,
        note: `Project "${c.project_name}" not found in ConstraintsPro`
      });
      continue;
    }

    // Check for duplicates
    const existing = existingByProject[c.project_name] || [];
    // Filter to only open/in_progress constraints for dup checking
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
      console.log(`    Matches existing: ${dupMatch.description.substring(0, 60)}...`);
      results.push({
        description: c.description,
        project_name: c.project_name,
        priority: c.priority,
        status: "duplicate",
        constraint_id: dupMatch._id,
        note: `Similar to existing constraint: "${dupMatch.description.substring(0, 80)}..."`
      });
      continue;
    }

    // Create the constraint
    try {
      const discipline = categoryToDiscipline(c.category);
      const createArgs = {
        projectId: projId,
        discipline: discipline,
        description: c.description,
        priority: c.priority.toLowerCase(),
        owner: c.owner,
        status: "open",
        userId: creatorUserId,
      };

      // Add dueDate if present (convert to Unix ms)
      if (c.need_by_date) {
        createArgs.dueDate = new Date(c.need_by_date).getTime();
      }

      const constraintId = await convexMutation("constraints:create", createArgs);
      console.log(`  CREATED: ${c.description.substring(0, 60)}... -> ${constraintId}`);

      // Step 4: Add note
      try {
        await convexMutation("constraints:addNote", {
          constraintId: constraintId,
          content: "Auto-logged from email — From: Joshua.Hauger@mastec.com | Subject: DSC - 2/27 Production & Constraints",
          userId: creatorUserId,
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
        note: null
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

  // Output summary
  console.log("\n=== SUMMARY ===");
  const created = results.filter(r => r.status === "created").length;
  const duplicates = results.filter(r => r.status === "duplicate").length;
  const failed = results.filter(r => r.status === "failed").length;
  console.log(`Created: ${created} | Duplicates: ${duplicates} | Failed: ${failed}`);

  console.log("\n```json");
  console.log(JSON.stringify(results, null, 2));
  console.log("```");
}

main().catch(err => {
  console.error("FATAL ERROR:", err);
  process.exit(1);
});
