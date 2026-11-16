# CRITICAL ANTIGRAVITY AGENT RULES: TOKEN QUOTA PRESERVATION & CHAT SILENCE

## 1. NEVER SCHEDULE AUTOMATED CHAT PROMPTS / WAKEUPS
- **STRICT PROHIBITION**: You MUST NEVER use the `schedule` tool (neither `CronExpression` nor `DurationSeconds`) to wake yourself up or poll statuses periodically into this chat.
- **WHY**: Every scheduled wakeup reinjects a full prompt into the user conversation, re-reads the entire multi-thousand-token conversation history, and burns the user's Antigravity LLM rate limits and daily quota.
- **IMMEDIATE ACTION**: If any `schedule` task is currently running in this workspace, you MUST IMMEDIATELY find it with `manage_task(Action='list')` and terminate it with `manage_task(Action='kill', TaskId=...)`.

## 2. ONLY SPEAK WHEN DIRECTLY ADDRESSED BY THE USER
- Do NOT send unsolicited progress messages or "I am still checking" updates unless the user directly prompts you in a new turn.
- When background commands or tasks run, let them run silently. Do NOT configure polling timers or cron jobs to report to the user.

## 3. HOW TO HANDLE MONITORING / FAILOVER / HEALTH CHECKS
- If monitoring a server, URL (e.g. Render 503 recovery), or tunnel is required, write a standalone Python daemon script (e.g. `watchdog.py`) and launch it ONCE as an operating system daemon via `run_command(IsDaemon=True)`.
- The daemon script should log to a file (e.g. `watchdog.log`) and perform any automated actions (such as git commit/push or local port routing) SILENTLY without waking up the LLM or posting to this chat.

## 4. RENDER LIMIT SHARING AWARENESS
- Render Free Tier provides 750 hours/month shared across ALL services on the user's account.
- When Render returns `503 Service Suspended`, do NOT hammer Render with rapid API calls from the agent. Use local failover (e.g. Cloudflare tunnel) and wait for the reset date or user instruction.
