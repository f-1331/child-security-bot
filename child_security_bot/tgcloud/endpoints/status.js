import { db, fetch } from 'sdk';
import { edgeUsers, edgeEvents } from '../schema.js';
import { WEBAPP_URL } from '../lib/bridge.js';

export default async function (input, ctx) {
  const user = ctx?.initData?.user || null;
  const [userCount, eventCount] = await Promise.all([
    db.$count(edgeUsers),
    db.$count(edgeEvents),
  ]);

  let vercelHealth = { status: 'unknown' };
  try {
    const res = await fetch(`${WEBAPP_URL}/health`);
    if (res.ok) vercelHealth = await res.json();
    else vercelHealth = { status: 'error', httpStatus: res.status };
  } catch (e) {
    vercelHealth = { status: 'unreachable', error: e?.message || 'fetch failed' };
  }

  return {
    platform: 'tgcloud-serverless-edge',
    caller: user ? { id: user.id, first_name: user.first_name, username: user.username } : null,
    edge_stats: { users: userCount, routed_events: eventCount },
    vercel_backend: vercelHealth,
    webapp_url: WEBAPP_URL,
  };
}
