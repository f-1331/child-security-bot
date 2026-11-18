import { db, fetch } from 'sdk';
import { ownerEdgeUsers, ownerEdgeEvents } from '../schema.js';
import { WEBAPP_URL } from '../lib/bridge.js';

export default async function (input, ctx) {
  const user = ctx?.initData?.user || null;
  const [adminCount, eventCount] = await Promise.all([
    db.$count(ownerEdgeUsers),
    db.$count(ownerEdgeEvents),
  ]);

  let vercelHealth = { status: 'unknown' };
  let neonHealth = { status: 'unknown' };
  try {
    const [hRes, nRes] = await Promise.all([
      fetch(`${WEBAPP_URL}/health`),
      fetch(`${WEBAPP_URL}/api/neon/status`),
    ]);
    vercelHealth = hRes.ok ? await hRes.json() : { status: 'error', httpStatus: hRes.status };
    neonHealth = nRes.ok ? await nRes.json() : { status: 'error', httpStatus: nRes.status };
  } catch (e) {
    vercelHealth = { status: 'unreachable', error: e?.message || 'fetch failed' };
  }

  return {
    platform: 'tgcloud-owner-console-edge',
    caller: user ? { id: user.id, first_name: user.first_name, username: user.username } : null,
    edge_stats: { users: adminCount, routed_events: eventCount },
    vercel_backend: vercelHealth,
    neon_database: neonHealth?.services?.postgres || neonHealth,
  };
}
