import { forwardOwnerUpdate } from '../lib/bridge.js';

export default async function (message, ctx) {
  return await forwardOwnerUpdate(message, ctx, 'message');
}
