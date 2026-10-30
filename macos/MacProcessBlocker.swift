import Foundation
import AppKit

/**
 * 100% Offline App & Game Blocker for macOS:
 * - Monitors running processes via NSWorkspace.shared.runningApplications
 * - Terminates blocked apps during Study Time or Bedtime (Steam, Roblox, Minecraft, Discord, etc.)
 * - Fullscreen Lock Overlay at .screenSaver window level
 * - Tracks active window title and screen time per app
 */
public final class MacProcessBlocker {

    public static let shared = MacProcessBlocker()

    private var timer: Timer?
    private var isLockdownActive = false
    private var lockReason = "Dars vaqti"
    private var overlayWindow: NSWindow?

    private let blacklistedApps: Set<String> = [
        "steam", "roblox", "minecraft", "discord", "telegram",
        "epicgameslauncher", "battlenet", "riotclient", "leagueclient",
        "dota2", "cs2", "valorant"
    ]

    private init() {}

    public func startMonitoring() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            self?.checkRunningApplications()
        }
    }

    public func stopMonitoring() {
        timer?.invalidate()
        timer = nil
    }

    public func setLockdown(enabled: Bool, reason: String = "Dars vaqti") {
        isLockdownActive = enabled
        lockReason = reason
        if enabled {
            showLockOverlay(reason: reason)
        } else {
            dismissLockOverlay()
        }
    }

    private func checkRunningApplications() {
        let runningApps = NSWorkspace.shared.runningApplications

        // Check active foreground window title
        if let frontApp = NSWorkspace.shared.frontmostApplication {
            let appName = frontApp.localizedName ?? frontApp.bundleIdentifier ?? "App"
            recordAppUsage(name: appName)
        }

        // If total lockdown active, block all non-system apps
        if isLockdownActive {
            for app in runningApps {
                guard let bundleId = app.bundleIdentifier?.lowercased() else { continue }
                if isNonSystemApp(bundleId: bundleId) {
                    app.terminate()
                }
            }
            return
        }

        // Check selective gaming blacklist
        for app in runningApps {
            guard let name = app.localizedName?.lowercased() ?? app.bundleIdentifier?.lowercased() else { continue }
            for blocked in blacklistedApps {
                if name.contains(blocked) {
                    NSLog("[MacProcessBlocker] Terminating blocked game: \(name)")
                    app.terminate()
                    showLockOverlay(reason: "«\(app.localizedName ?? "O'yin")» ota-onangiz tomonidan cheklangan!")
                    break
                }
            }
        }
    }

    private func isNonSystemApp(bundleId: String) -> Bool {
        return !bundleId.contains("apple") &&
               !bundleId.contains("childsecurity") &&
               !bundleId.contains("systemuiserver") &&
               !bundleId.contains("finder")
    }

    private func recordAppUsage(name: String) {
        var usage = UserDefaults.standard.dictionary(forKey: "mac_app_usage") as? [String: Int] ?? [:]
        usage[name] = (usage[name] ?? 0) + 1
        UserDefaults.standard.set(usage, forKey: "mac_app_usage")
    }

    // MARK: - Fullscreen Lock Overlay Window
    private func showLockOverlay(reason: String) {
        DispatchQueue.main.async {
            if self.overlayWindow == nil {
                guard let screen = NSScreen.main else { return }
                let window = NSWindow(
                    contentRect: screen.frame,
                    styleMask: [.borderless],
                    backing: .buffered,
                    defer: false
                )
                window.level = .screenSaver // Above all apps & menu bar
                window.isOpaque = true
                window.backgroundColor = NSColor(red: 0.04, green: 0.05, blue: 0.09, alpha: 1.0)
                window.ignoresMouseEvents = false

                let view = NSView(frame: screen.frame)

                let label = NSTextField(labelWithString: "🛑 Dars Vaqti! MacBook vaqtincha cheklandi")
                label.font = NSFont.boldSystemFont(ofSize: 26)
                label.textColor = .white
                label.alignment = .center
                label.frame = NSRect(x: 0, y: screen.frame.height / 2 + 20, width: screen.frame.width, height: 40)
                view.addSubview(label)

                let reasonLabel = NSTextField(labelWithString: reason)
                reasonLabel.font = NSFont.systemFont(ofSize: 16)
                reasonLabel.textColor = NSColor(red: 0.22, green: 0.74, blue: 0.97, alpha: 1.0)
                reasonLabel.alignment = .center
                reasonLabel.frame = NSRect(x: 0, y: screen.frame.height / 2 - 30, width: screen.frame.width, height: 30)
                view.addSubview(reasonLabel)

                window.contentView = view
                window.makeKeyAndOrderFront(nil)
                self.overlayWindow = window
            }
        }
    }

    private func dismissLockOverlay() {
        DispatchQueue.main.async {
            self.overlayWindow?.orderOut(nil)
            self.overlayWindow = nil
        }
    }
}
