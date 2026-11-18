import { forwardUpdate } from '../lib/bridge.js';

export default async function (message, ctx) {
  return await forwardUpdate(message, ctx, 'message');
}
