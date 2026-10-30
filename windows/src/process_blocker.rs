use std::collections::HashSet;
use std::ffi::OsString;
use std::os::windows::ffi::OsStringExt;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;
use windows::Win32::Foundation::{CloseHandle, HANDLE, HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};
use windows::Win32::System::Threading::{OpenProcess, TerminateProcess, PROCESS_TERMINATE};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DispatchMessageW, GetForegroundWindow, GetMessageW,
    GetSystemMetrics, GetWindowTextW, RegisterClassW, SetWindowPos, ShowWindow, HWND_TOPMOST,
    MSG, SM_CXSCREEN, SM_CYSCREEN, SWP_SHOWWINDOW, SW_SHOW, WINDOW_EX_STYLE, WINDOW_STYLE,
    WNDCLASSW, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_POPUP,
};

pub struct ProcessBlocker {
    is_locked: Arc<AtomicBool>,
    blacklisted_names: HashSet<String>,
}

impl ProcessBlocker {
    pub fn new() -> Self {
        let mut list = HashSet::new();
        for app in &[
            "steam.exe",
            "cs2.exe",
            "dota2.exe",
            "robloxplayerbeta.exe",
            "javaw.exe",
            "minecraft.exe",
            "epicgameslauncher.exe",
            "discord.exe",
            "telegram.exe",
            "battlenet.exe",
            "riotclientservices.exe",
        ] {
            list.insert(app.to_string());
        }

        Self {
            is_locked: Arc::new(AtomicBool::new(false)),
            blacklisted_names: list,
        }
    }

    pub fn start_monitor(&self) {
        let is_locked = self.is_locked.clone();
        let blacklisted = self.blacklisted_names.clone();

        thread::spawn(move || loop {
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
                            let exe_name = OsString::from_wide(&entry.szExeFile[..len])
                                .to_string_lossy()
                                .to_lowercase();

                            let locked = is_locked.load(Ordering::Relaxed);
                            if locked || blacklisted.contains(&exe_name) {
                                if !is_essential_system_proc(&exe_name) {
                                    if let Ok(h_proc) =
                                        OpenProcess(PROCESS_TERMINATE, false, entry.th32ProcessID)
                                    {
                                        let _ = TerminateProcess(h_proc, 1);
                                        let _ = CloseHandle(h_proc);
                                        println!(
                                            "[Windows Blocker] Terminated blocked process: {}",
                                            exe_name
                                        );
                                    }
                                }
                            }

                            if Process32NextW(snapshot, &mut entry).is_err() {
                                break;
                            }
                        }
                    }
                    let _ = CloseHandle(snapshot);
                }

                // Track active window title
                let fg_hwnd = GetForegroundWindow();
                if fg_hwnd.0 != 0 {
                    let mut title_buf = [0u16; 512];
                    let len = GetWindowTextW(fg_hwnd, &mut title_buf);
                    if len > 0 {
                        let title = OsString::from_wide(&title_buf[..len as usize])
                            .to_string_lossy()
                            .to_string();
                        // Active window tracked
                    }
                }
            }

            thread::sleep(Duration::from_millis(1000));
        });
    }

    pub fn set_lockdown(&self, enable: bool) {
        self.is_locked.store(enable, Ordering::SeqCst);
        if enable {
            spawn_fullscreen_lock_overlay();
        }
    }
}

fn is_essential_system_proc(name: &str) -> bool {
    matches!(
        name,
        "explorer.exe"
            | "system"
            | "smss.exe"
            | "csrss.exe"
            | "wininit.exe"
            | "services.exe"
            | "lsass.exe"
            | "svchost.exe"
            | "child_security_windows.exe"
    )
}

fn spawn_fullscreen_lock_overlay() {
    thread::spawn(|| unsafe {
        let class_name: Vec<u16> = "ChildSecurityLockOverlay\0".encode_utf16().collect();
        let wnd_class = WNDCLASSW {
            lpfnWndProc: Some(overlay_wnd_proc),
            hInstance: windows::Win32::System::LibraryLoader::GetModuleHandleW(None)
                .unwrap_or_default(),
            lpszClassName: windows::core::PCWSTR(class_name.as_ptr()),
            ..Default::default()
        };

        RegisterClassW(&wnd_class);

        let screen_w = GetSystemMetrics(SM_CXSCREEN);
        let screen_h = GetSystemMetrics(SM_CYSCREEN);

        let hwnd = CreateWindowExW(
            WINDOW_EX_STYLE(WS_EX_TOPMOST.0 | WS_EX_TOOLWINDOW.0),
            windows::core::PCWSTR(class_name.as_ptr()),
            windows::core::PCWSTR("🛑 Dars Vaqti - Kompyuter Qulflangan\0".encode_utf16().collect::<Vec<u16>>().as_ptr()),
            WINDOW_STYLE(WS_POPUP.0),
            0,
            0,
            screen_w,
            screen_h,
            HWND(0),
            None,
            wnd_class.hInstance,
            None,
        );

        if hwnd.0 != 0 {
            SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                0,
                0,
                screen_w,
                screen_h,
                SWP_SHOWWINDOW,
            );
            ShowWindow(hwnd, SW_SHOW);

            let mut msg = MSG::default();
            while GetMessageW(&mut msg, HWND(0), 0, 0).as_bool() {
                DispatchMessageW(&msg);
            }
        }
    });
}

unsafe extern "system" fn overlay_wnd_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    DefWindowProcW(hwnd, msg, wparam, lparam)
}
