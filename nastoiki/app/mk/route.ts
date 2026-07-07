import { QTICKETS_URL, qt } from "../lib";

// Short branded link the agent hands out in chat (nastoiki.pro/mk). 302-redirects
// to the qtickets master-class page, tagged utm_medium=agent for attribution.
export const dynamic = "force-static";

export function GET() {
  return Response.redirect(qt(QTICKETS_URL, "agent"), 302);
}
