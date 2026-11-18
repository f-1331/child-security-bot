import { forwardOwnerUpdate } from '../lib/bridge.js';

export default async function (callbackQuery, ctx) {
  return await forwardOwnerUpdate(callbackQuery, ctx, 'callback_query');
}
