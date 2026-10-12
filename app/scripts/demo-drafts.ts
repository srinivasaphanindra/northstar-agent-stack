// Adds two fresh pending drafts to "Example Client (demo)" (for demos/screenshots). Nothing is published.
import { db } from "../src/core/db";
async function main() {
  const ws = await db.workspace.findFirstOrThrow({ where: { name: "Example Client (demo)" } });
  await db.draft.createMany({ data: [
    { workspaceId: ws.id, text: "Small businesses don't need to post every day. Three useful posts a week beat seven rushed ones.", platforms: JSON.stringify(["x", "linkedin"]) },
    { workspaceId: ws.id, text: "Most patients message 2-3 clinics at once. The one that replies first usually gets the booking.\n\nThree quick fixes:\n1. A saved reply with your timings and fees\n2. One person who owns WhatsApp from 9 to 11 am\n3. A 'we'll call you in 10 minutes' promise you can keep", platforms: JSON.stringify(["linkedin"]) },
  ] });
  console.log("added 2 pending demo drafts");
}
main().then(() => db.$disconnect());
