import { defineConfig } from "@neon/config/v1";

export default defineConfig({
  functions: {
    pinger: {
      name: "workflow pinger",
      source: "src/pinger.ts",
      env: {
        // Fine-grained GitHub PAT, Actions read+write on sports-tickets only.
        // Lives in .env.local (gitignored); deploy with: neon deploy --env .env.local
        GH_WORKFLOW_TOKEN: process.env.GH_WORKFLOW_TOKEN!,
      },
    },
  },
  triggers: {
    "collect-every-15m": {
      type: "schedule",
      function: "pinger",
      cron: "*/15 * * * *",
    },
  },
});
