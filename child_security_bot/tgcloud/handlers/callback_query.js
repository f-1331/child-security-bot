import { forwardUpdate } from '../lib/bridge.js';

export default async function (callbackQuery, ctx) {
  return await forwardUpdate(callbackQuery, ctx, 'callback_query');
}
