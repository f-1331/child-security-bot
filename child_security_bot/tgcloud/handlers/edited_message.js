import { forwardUpdate } from '../lib/bridge.js';

export default async function (editedMessage, ctx) {
  return await forwardUpdate(editedMessage, ctx, 'edited_message');
}
