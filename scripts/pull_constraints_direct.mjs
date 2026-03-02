#!/usr/bin/env node
/**
 * Direct Convex query to pull ALL open constraints across all projects.
 * Outputs JSON to stdout for PDF generation.
 */
import { ConvexHttpClient } from "convex/browser";
import { anyApi } from "convex/server";

const CONVEX_URL = "https://charming-cuttlefish-923.convex.cloud";
const client = new ConvexHttpClient(CONVEX_URL);

async function main() {
  // 1. Get all projects
  const projects = await client.query(anyApi.projects.list, {});
  console.error(`Found ${projects.length} projects`);

  // 2. Pull constraints for each project
  const allConstraints = [];

  for (const project of projects) {
    try {
      const constraints = await client.query(anyApi.constraints.listByProject, {
        projectId: project._id,
      });

      // Filter to open/in-progress only
      const openConstraints = constraints.filter(
        (c) => c.status !== "resolved" && c.status !== "closed"
      );

      console.error(`  ${project.name}: ${openConstraints.length} open constraints`);

      for (const c of openConstraints) {
        // Calculate days open
        const createdAt = c._creationTime || c.createdAt;
        const daysOpen = createdAt
          ? Math.floor((Date.now() - createdAt) / (1000 * 60 * 60 * 24))
          : null;

        allConstraints.push({
          id: c._id,
          project: project.name,
          project_key: project.code || project.name.toLowerCase().replace(/\s+/g, "-"),
          description: c.description || "No description",
          owner: c.dscLeadName || c.owner || "Unassigned",
          priority: (c.priority || "MEDIUM").toUpperCase(),
          status: c.status || "open",
          need_by_date: c.dueDate || null,
          days_open: daysOpen,
          notes: c.latestNote || "",
          discipline: c.discipline || "",
          category: c.discipline || "",
        });
      }
    } catch (err) {
      console.error(`  Error fetching ${project.name}: ${err.message}`);
    }
  }

  console.error(`\nTotal open constraints: ${allConstraints.length}`);

  // Output JSON to stdout
  console.log(JSON.stringify(allConstraints, null, 2));
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
