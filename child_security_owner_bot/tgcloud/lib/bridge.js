import { db, api, fetch } from 'sdk';
import { sql } from 'sdk/db';
import { ownerEdgeUsers, ownerEdgeEvents } from '../schema.js';

export const OWNER_WEBHOOK_URL = 'https://child-security-bot.vercel.app/api/telegram/owner-webhook';
export const WEBAPP_URL = 'https://child-security-bot.vercel.app';
const BRIDGE_SECRET = '30c6db3749a3aca7c4908a795703e838';

async function touchOwnerEdgeUser(from) {
  if (!from || !from.id) return;
  try {
    await db.insert(ownerEdgeUsers).values({
      tgId: from.id,
      username: from.username || null,
      firstName: from.first_name || null,
      commandCount: 1,
    }).onConflictDoUpdate({
      target: ownerEdgeUsers.tgId,
      set: {
        username: from.username || null,
        firstName: from.first_name || null,
        commandCount: sql`${ownerEdgeUsers.commandCount} + 1`,
        lastSeenAt: new Date(),
      },
    }).run();
  } catch (err) {
    console.warn('touchOwnerEdgeUser warning:', err?.message || err);
  }
}

async function logOwnerEdgeEvent(updateId, updateType, tgId, backendStatus, latencyMs) {
  try {
    await db.insert(ownerEdgeEvents).values({
      updateId: updateId || 0,
      updateType,
      tgId: tgId || null,
      backendStatus,
      latencyMs,
    }).run();
  } catch (err) {
    console.warn('logOwnerEdgeEvent warning:', err?.message || err);
  }
}

async function sendOwnerEdgeFallback(update, updateType) {
  try {
    if (updateType === 'message' && update.message?.chat?.id) {
      await api.sendMessage({
        chat_id: update.message.chat.id,
        text:
          '👑 <b>Smart Parental Control — Owner Console (Edge)</b>\n\n' +
          '⚡ Asosiy Vercel boshqaruv serveri uyg\'onmoqda. Iltimos, 2 soniyadan so\'ng qayta bosing.',
        parse_mode: 'HTML',
      });
    } else if (updateType === 'callback_query' && update.callback_query?.id) {
      await api.answerCallbackQuery({
        callback_query_id: update.callback_query.id,
        text: '⚡ Owner server uyg\'onmoqda, qayta bosing...',
        show_alert: false,
      });
    }
  } catch (err) {
    console.error('Owner edge fallback error:', err?.message || err);
  }
}

export async function forwardOwnerUpdate(payload, ctx, updateType) {
  const started = Date.now();
  const update = ctx?.update || { update_id: 0, [updateType]: payload };
  const from = payload?.from || update?.message?.from || update?.callback_query?.from || null;

  await touchOwnerEdgeUser(from);

  let status = 500;
  try {
    const res = await fetch(OWNER_WEBHOOK_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Telegram-Bot-Api-Secret-Token': BRIDGE_SECRET,
      },
      body: JSON.stringify(update),
    });
    status = res.status;
    if (!res.ok) {
      const errBody = await res.text();
      console.error(`Owner Vercel webhook HTTP ${status}:`, errBody.slice(0, 300));
      await sendOwnerEdgeFallback(update, updateType);
    }
  } catch (err) {
    console.error('Owner Vercel bridge fetch failed:', err?.message || err);
    status = 502;
    await sendOwnerEdgeFallback(update, updateType);
  }

  const latencyMs = Date.now() - started;
  await logOwnerEdgeEvent(update.update_id, updateType, from?.id, status, latencyMs);
  return { ok: status >= 200 && status < 300, status, latencyMs };
}
