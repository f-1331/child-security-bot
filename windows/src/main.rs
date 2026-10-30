mod firewall;
mod process_blocker;
mod watchdog_service;

use firewall::WindowsFirewall;
use process_blocker::ProcessBlocker;
use std::thread;
use std::time::Duration;
use watchdog_service::WatchdogService;
use windows::Win32::System::Power::{GetSystemPowerStatus, SYSTEM_POWER_STATUS};
use windows::Win32::System::SystemInformation::GetTickCount64;

fn main() {
    println!("🛡️ Child Security Windows Desktop Agent starting...");

    // 1. Initialize Watchdog (resurrect UI if killed)
    let watchdog = WatchdogService::new("child_security_windows.exe");
    watchdog.start_watchdog();

    // 2. Initialize Process & Game Blocker
    let blocker = ProcessBlocker::new();
    blocker.start_monitor();

    // 3. Telemetry loop (Battery, Monotonic clock anti-tamper, and server heartbeat)
    let child_id = std::env::var("CHILD_ID").unwrap_or_else(|_| "1001".to_string());
    let server_url = std::env::var("SERVER_URL")
        .unwrap_or_else(|_| "https://child-security-site.onrender.com".to_string());

    let mut last_wall_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64;
    let mut last_mono_ms = unsafe { GetTickCount64() } as i64;

    loop {
        // Battery Telemetry
        let mut power_status = SYSTEM_POWER_STATUS::default();
        let (battery_level, is_charging) = unsafe {
            if GetSystemPowerStatus(&mut power_status).as_bool() {
                let batt = if power_status.BatteryLifePercent <= 100 {
                    power_status.BatteryLifePercent as i32
                } else {
                    100
                };
                let charging = power_status.ACLineStatus == 1;
                (batt, charging)
            } else {
                (100, true)
            }
        };

        // Anti-Tamper: Detect system clock shift
        let cur_wall_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as i64;
        let cur_mono_ms = unsafe { GetTickCount64() } as i64;

        let wall_delta = cur_wall_ms - last_wall_ms;
        let mono_delta = cur_mono_ms - last_mono_ms;

        if mono_delta >= 5000 && (wall_delta < -30000 || (wall_delta - mono_delta).abs() > 1800000)
        {
            println!("⚠️ Tamper detected: Windows system clock shifted manually!");
            blocker.set_lockdown(true);
        }
        last_wall_ms = cur_wall_ms;
        last_mono_ms = cur_mono_ms;

        // Send telemetry ping
        let payload = serde_json::json!({
            "latitude": 0.0,
            "longitude": 0.0,
            "accuracy": 100.0,
            "speed": 0,
            "speed_kmh": 0,
            "battery_level": battery_level,
            "is_charging": is_charging,
            "sound_mode": "normal",
            "device_model": "Windows PC / Laptop"
        });

        let endpoint = format!("{}/api/v1/webapp/child/{}/location", server_url, child_id);
        let _ = ureq::post(&endpoint)
            .set("Content-Type", "application/json")
            .timeout(Duration::from_secs(8))
            .send_json(payload);

        thread::sleep(Duration::from_secs(30));
    }
}
