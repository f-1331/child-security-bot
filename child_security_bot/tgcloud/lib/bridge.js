import { db, api, fetch } from 'sdk';
import { sql } from 'sdk/db';
import { edgeUsers, edgeEvents } from '../schema.js';

export const VERCEL_WEBHOOK_URL = 'https://child-security-bot.vercel.app/api/telegram/webhook';
export const WEBAPP_URL = 'https://child-security-bot.vercel.app';
const BRIDGE_SECRET = '30c6db3749a3aca7c4908a795703e838';

async function touchEdgeUser(from) {
  if (!from || !from.id) return;
  try {
    await db.insert(edgeUsers).values({
      tgId: from.id,
      username: from.username || null,
      firstName: from.first_name || null,
      messageCount: 1,
    }).onConflictDoUpdate({
      target: edgeUsers.tgId,
      set: {
        username: from.username || null,
        firstName: from.first_name || null,
        messageCount: sql`${edgeUsers.messageCount} + 1`,
        lastSeenAt: new Date(),
      },
    }).run();
  } catch (err) {
    console.warn('touchEdgeUser warning:', err?.message || err);
  }
}

async function logEdgeEvent(updateId, updateType, tgId, backendStatus, latencyMs) {
  try {
    await db.insert(edgeEvents).values({
      updateId: updateId || 0,
      updateType,
      tgId: tgId || null,
      backendStatus,
      latencyMs,
    }).run();
  } catch (err) {
    console.warn('logEdgeEvent warning:', err?.message || err);
  }
}

async function sendEdgeFallback(update, updateType) {
  try {
    if (updateType === 'message' && update.message?.chat?.id) {
      await api.sendMessage({
        chat_id: update.message.chat.id,
        text:
          '🛡️ <b>Smart Parental Control (Telegram Serverless Edge)</b>\n\n' +
          '⚡ Asosiy server bir soniyaga uyg\'onmoqda. Iltimos, 2 soniyadan so\'ng qayta urinib ko\'ring yoki pastdagi tugma orqali Web App ni oching:',
        parse_mode: 'HTML',
        reply_markup: {
          inline_keyboard: [
            [{ text: '🌐 Web App ni ochish', web_app: { url: WEBAPP_URL } }],
          ],
        },
      });
    } else if (updateType === 'callback_query' && update.callback_query?.id) {
      await api.answerCallbackQuery({
        callback_query_id: update.callback_query.id,
        text: '⚡ Server uyg\'onmoqda, iltimos qayta bosing...',
        show_alert: false,
      });
    }
  } catch (err) {
    console.error('Edge fallback error:', err?.message || err);
  }
}

export async function forwardUpdate(payload, ctx, updateType) {
  const started = Date.now();
  const update = ctx?.update || { update_id: 0, [updateType]: payload };
  const from = payload?.from || update?.message?.from || update?.callback_query?.from || null;

  // 1. Track user at Telegram Serverless Edge (built-in SQLite)
  await touchEdgeUser(from);

  // 2. Forward raw Telegram Update to full Python FastAPI backend on Vercel Serverless
  let status = 500;
  try {
    const res = await fetch(VERCEL_WEBHOOK_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Telegram-Bot-Api-Secret-Token': BRIDGE_SECRET,
        'X-Tgcloud-Bridge-Secret': BRIDGE_SECRET,
      },
      body: JSON.stringify(update),
    });
    status = res.status;
    if (!res.ok) {
      const errBody = await res.text();
      console.error(`Vercel webhook HTTP ${status}:`, errBody.slice(0, 300));
      await sendEdgeFallback(update, updateType);
    }
  } catch (err) {
    console.error('Vercel bridge fetch failed:', err?.message || err);
    status = 502;
    await sendEdgeFallback(update, updateType);
  }

  const latencyMs = Date.now() - started;
  await logEdgeEvent(update.update_id, updateType, from?.id, status, latencyMs);
  return { ok: status >= 200 && status < 300, status, latencyMs };
}
