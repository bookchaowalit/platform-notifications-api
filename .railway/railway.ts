import { defineRailway, project, service } from "railway/iac";

// platform-notifications-api: the notifications cell. Variables stay unmanaged (secrets live in Railway only).
// notifications-api sleeps when idle (Railway Serverless) and wakes on the next request.
// notifications-dispatcher is a cron run that evaluates the rules against the tasks cell,
// queues what matched and delivers pending entries to Telegram, then exits.
// No graph publisher: notification log entries are operational records, not graph nodes.
// Every service deploys from GitHub main after CI passes (Railway Wait for CI, 2026-10-18).
// Declared here so `railway config apply` keeps the connection instead of removing it.
const github = {
  type: "github" as const,
  repo: "bookchaowalit/platform-notifications-api",
  branch: "main",
  checkSuites: true,
};

export default defineRailway(() => {
  const api = service("notifications-api", {
    source: github,
    healthcheck: "/health",
    healthcheckTimeout: 60,
    deploy: { sleepApplication: true },
  });
  const dispatcher = service("notifications-dispatcher", {
    source: github,
    startCommand: "notifications-dispatch",
    deploy: { cronSchedule: "*/30 * * * *", restartPolicyType: "NEVER" },
  });

  return project("platform-notifications-api", {
    variables: { managed: false },
    resources: [api, dispatcher],
  });
});
