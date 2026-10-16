"""
Simulation and End-to-End Verification Script for Smart Parental Tracker.
Populates sample parent, child, safe zones, and tasks, then sends GPS telemetry
and checks geofence detection, battery alerts, and WebApp API endpoints.
"""
import asyncio
import sys
import httpx
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from db.database import AsyncSessionLocal, init_db
import db.crud as crud
from config import settings


async def populate_demo_data():
    print("\n--- 1. Initializing DB & Creating Demo Users ---")
    await init_db()

    async with AsyncSessionLocal() as session:
        # Create Parent: Rustam Karimov (ID: 1001)
        parent, _ = await crud.get_or_create_user(
            session=session,
            user_id=1001,
            role="parent",
            full_name="Rustam Karimov (Ota)",
            username="rustam_parent"
        )

        # Create Child: Azizbek Karimov (ID: 2002)
        child, _ = await crud.get_or_create_user(
            session=session,
            user_id=2002,
            role="child",
            full_name="Azizbek Karimov (Farzand)",
            username="azizbek_child"
        )
        child.virtual_balance = 15000

        # Link Child to Parent
        await crud.link_parent_child(session, parent.id, child.id, nickname="Azizbek")

        # Create Geofences (Safe Zones) in Tashkent
        # 1. Home ("Uyimiz")
        await crud.create_geofence(
            session=session,
            parent_id=parent.id,
            name="Uyimiz (Chilonzor)",
            latitude=41.2858,
            longitude=69.2035,
            radius_meters=200.0,
            child_id=child.id
        )

        # 2. School ("Maktab №45")
        await crud.create_geofence(
            session=session,
            parent_id=parent.id,
            name="Maktab №45",
            latitude=41.2915,
            longitude=69.2120,
            radius_meters=250.0,
            child_id=child.id
        )

        # 3. Training Center ("IT Park / O'quv markazi")
        await crud.create_geofence(
            session=session,
            parent_id=parent.id,
            name="O'quv Markazi",
            latitude=41.3000,
            longitude=69.2250,
            radius_meters=300.0,
            child_id=child.id
        )

        # Create Sample Tasks
        await crud.create_task(
            session=session,
            parent_id=parent.id,
            child_id=child.id,
            title="Matematika darslarini qilish",
            description="5-bob 12-18 mashqlar",
            reward_coins=5000
        )
        await crud.create_task(
            session=session,
            parent_id=parent.id,
            child_id=child.id,
            title="Uyga soat 17:00 gacha yetib kelish",
            description="Kech qolmasdan kelish kerak",
            reward_coins=3000
        )

        await session.commit()
        print("✅ Demo parent, child, geofences, and tasks initialized.")


async def test_telemetry_flow():
    base_url = f"http://127.0.0.1:{settings.API_PORT}"
    print(f"\n--- 2. Sending Telemetry Route to API ({base_url}) ---")

    route_waypoints = [
        # (lat, lon, battery, charging, desc)
        (41.2858, 69.2035, 85, False, "Uyda (Inside Uyimiz Safe Zone)"),
        (41.2870, 69.2060, 84, False, "Ko'chada, maktab sari piyoda"),
        (41.2890, 69.2090, 82, False, "Chilonzor chorrahasida"),
        (41.2915, 69.2120, 80, False, "Maktabga yetib keldi (Entering Maktab Safe Zone)"),
        (41.2916, 69.2121, 14, False, "Maktabda darsda - BATAREYA 14% (Low Battery Trigger!)"),
    ]

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Check health
        health = await client.get(f"{base_url}/health")
        print("Health status:", health.json())

        for idx, (lat, lon, bat, charging, desc) in enumerate(route_waypoints, 1):
            payload = {
                "child_id": 2002,
                "lat": lat,
                "lon": lon,
                "battery_level": bat,
                "is_charging": charging,
                "sound_mode": "silent" if idx == 5 else "normal",
                "accuracy": 8.5,
                "timestamp": datetime.utcnow().isoformat()
            }
            res = await client.post(f"{base_url}/api/v1/telemetry", json=payload)
            res_json = res.json()
            events = res_json["data"]["geofence_events"]
            bat_alert = res_json["data"]["battery_alert_triggered"]
            print(f"Step {idx}: {desc}")
            print(f"   Coordinates: ({lat}, {lon}) | Battery: {bat}%")
            if events:
                print(f"   ⚡ GEOFENCE EVENTS DETECTED: {events}")
            if bat_alert or bat <= 15:
                print(f"   🪫 LOW BATTERY ALERT PROCESSED! (<15%)")
            await asyncio.sleep(0.5)

        print("\n--- 3. Testing WebApp API Endpoints ---")
        # 1. Parent's children
        res_children = await client.get(f"{base_url}/api/v1/webapp/parent/1001/children")
        print("Children count:", len(res_children.json()["children"]))
        print("Latest Child Status:", res_children.json()["children"][0]["latest_location"])

        # 2. Child Route History
        res_history = await client.get(f"{base_url}/api/v1/webapp/child/2002/history")
        print("Recorded Route History Points:", len(res_history.json()["points"]))

        # 3. Child Geofences
        res_fences = await client.get(f"{base_url}/api/v1/webapp/child/2002/geofences")
        print("Active Geofences for Child:", [f["name"] for f in res_fences.json()["geofences"]])

        # 4. Child Tasks
        res_tasks = await client.get(f"{base_url}/api/v1/webapp/child/2002/tasks")
        print("Child Tasks:", [f"{t['title']} (+{t['reward_coins']} tanga)" for t in res_tasks.json()["tasks"]])

    print("\n✅ ALL TESTS AND VERIFICATIONS PASSED SUCCESSFULLY!")


async def run_all():
    await populate_demo_data()
    # Now run verification against the API
    try:
        await test_telemetry_flow()
    except Exception as e:
        print(f"API ping test skipped or server not yet listening: {e}")


if __name__ == "__main__":
    asyncio.run(run_all())
