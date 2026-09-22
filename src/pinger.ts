// Fires the GitHub Actions collect-snapshots workflow via workflow_dispatch.
// Invoked every 15 minutes by a Neon schedule trigger, because GitHub's own
// cron scheduler is best-effort and skips most */15 fires, while dispatched
// runs execute reliably within seconds.
import { parseTriggerInvocation } from "@neon/functions/triggers";

const REPO = "michaelmorningstar17/sports-tickets";
const WORKFLOW = "collect.yml";

export default {
  async fetch(request: Request): Promise<Response> {
    // Only genuine Neon trigger deliveries carry a verifiable
    // x-neon-trigger-invocation-id (the proxy strips client-sent ones).
    const parsed = await parseTriggerInvocation(request);
    if (!parsed.ok) {
      const status = parsed.error === "invalid_body" ? 400 : 401;
      return new Response(parsed.error, { status });
    }

    const res = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${process.env.GH_WORKFLOW_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "sports-tickets-pinger",
        },
        body: JSON.stringify({ ref: "main" }),
      },
    );

    // GitHub returns 204 on success. Log failures so `neon logs` shows them.
    if (res.status !== 204) {
      console.error(`dispatch failed: ${res.status} ${await res.text()}`);
      return new Response(`github returned ${res.status}`, { status: 502 });
    }
    console.log(`dispatched collect-snapshots at ${parsed.invocation.data.scheduledAt}`);
    return Response.json({ ok: true });
  },
};
