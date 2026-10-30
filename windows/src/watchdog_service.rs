use std::process::Command;
use std::thread;
use std::time::Duration;
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};
use windows::Win32::Foundation::CloseHandle;

/**
 * Windows Anti-Kill Watchdog Service:
 * - Runs as a resilient background worker (or NT SERVICE)
 * - Verifies the Child Security Tray UI process is alive
 * - If killed via Task Manager or taskkill, resurrects it within 500ms
 */
pub struct WatchdogService {
    ui_executable: String,
}

impl WatchdogService {
    pub fn new(ui_exe: &str) -> Self {
        Self {
            ui_executable: ui_exe.to_string(),
        }
    }

    pub fn start_watchdog(&self) {
        let exe = self.ui_executable.clone();
        thread::spawn(move || loop {
            let is_alive = check_process_running(&exe);
            if !is_alive {
                println!("[Watchdog] UI process terminated! Resurrecting within 500ms...");
                let _ = Command::new(&exe).spawn();
            }
            thread::sleep(Duration::from_millis(500));
        });
    }
}

fn check_process_running(target_exe: &str) -> bool {
    unsafe {
        if let Ok(snapshot) = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) {
            let mut entry = PROCESSENTRY32W::default();
            entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;

            if Process32FirstW(snapshot, &mut entry).is_ok() {
                loop {
                    let len = entry
                        .szExeFile
                        .iter()
                        .position(|&c| c == 0)
                        .unwrap_or(entry.szExeFile.len());
                    let exe_name = String::from_utf16_lossy(&entry.szExeFile[..len]).to_lowercase();

                    if exe_name == target_exe.to_lowercase() {
                        let _ = CloseHandle(snapshot);
                        return true;
                    }

                    if Process32NextW(snapshot, &mut entry).is_err() {
                        break;
                    }
                }
            }
            let _ = CloseHandle(snapshot);
        }
    }
    false
}
