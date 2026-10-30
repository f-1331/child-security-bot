import Foundation
import UIKit
#if canImport(FamilyControls)
import FamilyControls
#endif
#if canImport(ManagedSettings)
import ManagedSettings
#endif
#if canImport(DeviceActivity)
import DeviceActivity
#endif

/**
 * Screen Time & Offline App Blocker for iOS:
 * - iOS 15.0+: Apple Official FamilyControls & ManagedSettings (store.shield.applications)
 * - 100% OFFLINE schedule enforcement via DeviceActivityMonitor
 * - iOS 12.0 - 14.8 Fallback: Local VPN Tunnel + UIKit Fullscreen Lock Overlay
 */
public final class ScreenTimeBlocker {

    public static let shared = ScreenTimeBlocker()

    #if canImport(ManagedSettings)
    @available(iOS 15.0, *)
    private lazy var store = ManagedSettingsStore()
    #endif

    private init() {}

    // MARK: - Authorization (FamilyControls)
    public func requestAuthorization(completion: @escaping (Bool) -> Void) {
        #if canImport(FamilyControls)
        if #available(iOS 15.0, *) {
            Task {
                do {
                    try await AuthorizationCenter.shared.requestAuthorization(for: .individual)
                    completion(true)
                } catch {
                    NSLog("[ScreenTimeBlocker] FamilyControls auth failed: \(error.localizedDescription)")
                    completion(false)
                }
            }
            return
        }
        #endif
        // Fallback for iOS 12-14
        completion(true)
    }

    // MARK: - App Lockdown Enforcement
    public func setLockAllApps(enabled: Bool, reason: String = "Dars vaqti") {
        #if canImport(ManagedSettings)
        if #available(iOS 15.0, *) {
            if enabled {
                store.shield.applicationCategories = .all()
                store.media.denyExplicitContent = true
                NSLog("[ScreenTimeBlocker] All apps locked via ManagedSettingsStore")
            } else {
                store.shield.applicationCategories = nil
                store.shield.applications = nil
                NSLog("[ScreenTimeBlocker] All apps unlocked via ManagedSettingsStore")
            }
            return
        }
        #endif

        // iOS 12-14 Fallback: Activate Local Firewall & show Lock Overlay
        if enabled {
            LocalFirewallVPN.shared.startLockdown()
            showLockOverlay(reason: reason)
        } else {
            LocalFirewallVPN.shared.stopLockdown()
            dismissLockOverlay()
        }
    }

    // MARK: - Offline Bedtime Schedule
    public func updateBedtimeSchedule(startHour: Int, startMinute: Int, endHour: Int, endMinute: Int) {
        UserDefaults.standard.set(startHour, forKey: "bedtime_start_h")
        UserDefaults.standard.set(startMinute, forKey: "bedtime_start_m")
        UserDefaults.standard.set(endHour, forKey: "bedtime_end_h")
        UserDefaults.standard.set(endMinute, forKey: "bedtime_end_m")

        #if canImport(DeviceActivity)
        if #available(iOS 15.0, *) {
            let schedule = DeviceActivitySchedule(
                intervalStart: DateComponents(hour: startHour, minute: startMinute),
                intervalEnd: DateComponents(hour: endHour, minute: endMinute),
                repeats: true
            )
            let center = DeviceActivityCenter()
            let activityName = DeviceActivityName("uz.childsecurity.bedtime")
            do {
                try center.startMonitoring(activityName, during: schedule)
                NSLog("[ScreenTimeBlocker] DeviceActivity bedtime schedule armed 100% offline")
            } catch {
                NSLog("[ScreenTimeBlocker] Failed to set DeviceActivity schedule: \(error)")
            }
        }
        #endif
    }

    // MARK: - Lock Overlay UI (iOS 12-14 Fallback & Direct Prompt)
    private var overlayWindow: UIWindow?

    public func showLockOverlay(reason: String) {
        DispatchQueue.main.async {
            guard self.overlayWindow == nil else { return }
            let window: UIWindow
            if #available(iOS 13.0, *), let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene {
                window = UIWindow(windowScene: scene)
            } else {
                window = UIWindow(frame: UIScreen.main.bounds)
            }
            window.windowLevel = .alert + 1
            let vc = LockOverlayViewController()
            vc.reasonText = reason
            window.rootViewController = vc
            window.makeKeyAndVisible()
            self.overlayWindow = window
        }
    }

    public func dismissLockOverlay() {
        DispatchQueue.main.async {
            self.overlayWindow?.isHidden = true
            self.overlayWindow = nil
        }
    }
}

// MARK: - Fullscreen Lock Screen Controller
final class LockOverlayViewController: UIViewController {

    var reasonText: String = "Dars vaqti (Ota-ona nazorati)"

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(red: 0.04, green: 0.05, blue: 0.09, alpha: 1.0)

        let stack = UIStackView()
        stack.axis = .vertical
        stack.alignment = .center
        stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)

        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            stack.leadingAnchor.constraint(greaterThanOrEqualTo: view.leadingAnchor, constant: 32),
            stack.trailingAnchor.constraint(lessThanOrEqualTo: view.trailingAnchor, constant: -32)
        ])

        let iconLabel = UILabel()
        iconLabel.text = "🔒"
        iconLabel.font = .systemFont(ofSize: 64)
        stack.addArrangedSubview(iconLabel)

        let titleLabel = UILabel()
        titleLabel.text = "Qurilma Vaqtincha Bloklangan"
        titleLabel.textColor = .white
        titleLabel.font = .boldSystemFont(ofSize: 20)
        titleLabel.textAlignment = .center
        stack.addArrangedSubview(titleLabel)

        let reasonLabel = UILabel()
        reasonLabel.text = reasonText
        reasonLabel.textColor = UIColor(red: 0.22, green: 0.74, blue: 0.97, alpha: 1.0)
        reasonLabel.font = .systemFont(ofSize: 15, weight: .semibold)
        reasonLabel.textAlignment = .center
        reasonLabel.numberOfLines = 0
        stack.addArrangedSubview(reasonLabel)

        let infoLabel = UILabel()
        infoLabel.text = "Ota-onangiz ushbu vaqtda telefon cheklovini yoqqan.\nFaqat ota-onaga qo'ng'iroq va SOS ruxsat etiladi."
        infoLabel.textColor = UIColor(red: 0.58, green: 0.64, blue: 0.72, alpha: 1.0)
        infoLabel.font = .systemFont(ofSize: 13)
        infoLabel.textAlignment = .center
        infoLabel.numberOfLines = 0
        stack.addArrangedSubview(infoLabel)
    }
}
