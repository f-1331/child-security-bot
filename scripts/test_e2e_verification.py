import asyncio
import sys
from unittest.mock import AsyncMock, patch

sys.path.append('.')
from db.database import AsyncSessionLocal
import db.crud as crud
from api.webapp_api import child_sos_endpoint, child_cancel_sos_endpoint, send_child_quick_signal, QuickSignalRequest, ChildSOSRequest
from bot.handlers import parent_screen_time_handler, on_parent_school_mode_command, parent_ambient_sound_handler
import api.telemetry as telemetry

async def test_e2e():
    print("==================================================")
    print("STARTING E2E SYSTEM INTEGRATION TEST")
    print("==================================================")

    child_id = 997185613 # F.FUZAYL.
    parent_id = 6707839718 # fayzbek1331

    # Mock telegram bot to intercept all outgoing messages
    mock_bot = AsyncMock()
    mock_sent_msg = AsyncMock()
    mock_sent_msg.message_id = 998811
    mock_bot.send_message.return_value = mock_sent_msg
    mock_bot.send_location.return_value = mock_sent_msg

    with patch('api.telemetry.get_telegram_bot', return_value=mock_bot), \
         patch('api.webapp_api.get_owner_bot', return_value=mock_bot):

        async with AsyncSessionLocal() as session:
            # 1. Test SOS Emergency Dispatch
            print("\n1. Testing SOS Dispatch...")
            telemetry.last_sos_alert_map.pop(child_id, None)
            sos_req = ChildSOSRequest(
                is_sos_active=True,
                latitude=41.311081,
                longitude=69.240562,
                accuracy=12.0,
                battery_level=88
            )
            sos_res = await child_sos_endpoint(child_id=child_id, payload=sos_req, session=session)
            print("SOS Response:", sos_res)
            assert sos_res["status"] == "success", "SOS failed"
            assert mock_bot.send_message.called, "Telegram send_message was not called"
            print(f"SOS sent_message call count: {mock_bot.send_message.call_count}")
            # Check target chat
            first_chat_id = mock_bot.send_message.call_args_list[0][1].get("chat_id") or mock_bot.send_message.call_args_list[0][0][0]
            print(f"SOS target chat: {first_chat_id}")
            assert first_chat_id == parent_id, f"Expected {parent_id}, got {first_chat_id}"

            # 2. Test SOS Cancellation (Ensuring no deletion!)
            print("\n2. Testing SOS Cancellation (Audit Preservation)...")
            mock_bot.delete_message.reset_mock()
            mock_bot.send_message.reset_mock()
            cancel_res = await child_cancel_sos_endpoint(child_id=child_id, session=session)
            print("Cancel Response:", cancel_res)
            assert cancel_res["status"] == "success", "SOS Cancel failed"
            assert mock_bot.delete_message.call_count == 0, f"Error: delete_message was called {mock_bot.delete_message.call_count} times! Should be 0!"
            print("Verified: Previous SOS alert messages were NOT deleted from Telegram!")

            # 3. Test Quick Signal (Tezkor Signal)
            print("\n3. Testing Quick Signal Dispatch...")
            mock_bot.send_message.reset_mock()
            qs_payload = QuickSignalRequest(
                signal_type="school_arrived",
                latitude=41.311081,
                longitude=69.240562,
                battery_level=85
            )
            qs_res = await send_child_quick_signal(child_id=child_id, payload=qs_payload, session=session)
            print("Quick Signal Response:", qs_res)
            assert qs_res["status"] == "success", "Quick signal failed"
            assert mock_bot.send_message.called, "Quick signal Telegram dispatch failed"
            print("Verified: Quick Signal dispatched to parent!")

            # 4. Test Parent Handlers (Screen Time, School Mode, Ambient Sound)
            print("\n4. Testing Parent Telegram Bot Handlers...")
            mock_tg_msg = AsyncMock()
            mock_tg_msg.from_user.id = parent_id
            mock_tg_msg.from_user.username = "fayzbek1331"
            mock_tg_msg.from_user.full_name = "Fayzbek (Asoschi)"
            mock_tg_msg.text = "📱 Ilovalar & Ekran tahlili"
            mock_tg_msg.answer = AsyncMock()

            # Screen time
            await parent_screen_time_handler(mock_tg_msg, bot=mock_bot)
            assert mock_tg_msg.answer.called, "Screen time answer not called"
            print("Parent Screen Time: OK")

            # School mode
            mock_tg_msg.answer.reset_mock()
            mock_tg_msg.text = "📚 Maktab rejimi (Dars vaqti)"
            await on_parent_school_mode_command(mock_tg_msg, bot=mock_bot)
            assert mock_tg_msg.answer.called, "School mode answer not called"
            print("Parent School Mode: OK")

            # Ambient Sound
            mock_tg_msg.answer.reset_mock()
            mock_tg_msg.text = "🎙 Atrof ovozi"
            await parent_ambient_sound_handler(mock_tg_msg, bot=mock_bot)
            assert mock_tg_msg.answer.called, "Ambient sound answer not called"
            print("Parent Ambient Sound: OK")

            # 5. Test Walking Goal Setting
            print("\n5. Testing Walking Goal Update...")
            policy = await crud.get_or_create_device_policy(session, child_id)
            policy.daily_walking_goal_km = 4.0
            policy.daily_walking_goal_steps = 5712
            await session.commit()
            updated_policy = await crud.get_or_create_device_policy(session, child_id)
            assert updated_policy.daily_walking_goal_km == 4.0, "Walking goal km failed"
            assert updated_policy.daily_walking_goal_steps == 5712, "Walking goal steps failed"
            print(f"Walking Goal Updated: {updated_policy.daily_walking_goal_km} km ({updated_policy.daily_walking_goal_steps} steps)")

    print("\n==================================================")
    print("ALL E2E INTEGRATION TESTS PASSED 100% SUCCESSFULLY!")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(test_e2e())
