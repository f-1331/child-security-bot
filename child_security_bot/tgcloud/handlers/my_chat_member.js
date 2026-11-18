import { forwardUpdate } from '../lib/bridge.js';

export default async function (myChatMember, ctx) {
  return await forwardUpdate(myChatMember, ctx, 'my_chat_member');
}
