import { table, integer, text, index, sql } from 'sdk/db';

export const ownerEdgeUsers = table('owner_edge_users', {
  id:           integer('id').primaryKey({ autoIncrement: true }),
  tgId:         integer('tg_id').notNull().unique(),
  username:     text('username'),
  firstName:    text('first_name'),
  commandCount: integer('command_count').notNull().default(1),
  lastSeenAt:   integer('last_seen_at', { mode: 'timestamp' }).default(sql`(unixepoch())`),
}, (t) => ({
  lastSeenIdx: index('idx_owner_edge_users_last_seen').on(t.lastSeenAt),
}));

export const ownerEdgeEvents = table('owner_edge_events', {
  id:            integer('id').primaryKey({ autoIncrement: true }),
  updateId:      integer('update_id').notNull(),
  updateType:    text('update_type').notNull(),
  tgId:          integer('tg_id'),
  backendStatus: integer('backend_status').notNull().default(200),
  latencyMs:     integer('latency_ms').notNull().default(0),
  createdAt:     integer('created_at', { mode: 'timestamp' }).default(sql`(unixepoch())`),
}, (t) => ({
  createdIdx: index('idx_owner_edge_events_created').on(t.createdAt),
}));
