import UIKit
import BackgroundTasks
import UserNotifications

@main
class AppDelegate: UIResponder, UIApplicationDelegate, UNUserNotificationCenterDelegate {

    var window: UIWindow?

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
    ) -> Bool {

        // 1. Initialize Window & Root View Controller
        window = UIWindow(frame: UIScreen.main.bounds)
        let mainVC = WebBridgeViewController()
        window?.rootViewController = mainVC
        window?.makeKeyAndVisible()

        // 2. Start 24/7 CoreLocation background tracking
        LocationEngine.shared.startTracking()

        // 3. Register Background Task for offline sync
        if #available(iOS 13.0, *) {
            BGTaskScheduler.shared.register(
                forTaskWithIdentifier: "uz.childsecurity.app.sync",
                using: nil
            ) { task in
                self.handleBackgroundSync(task: task as! BGProcessingTask)
            }
        }

        // 4. Request notification permissions (including critical alerts for Siren)
        UNUserNotificationCenter.current().delegate = self
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge, .criticalAlert]) { granted, _ in
            if granted {
                DispatchQueue.main.async {
                    UIApplication.shared.registerForRemoteNotifications()
                }
            }
        }

        return true
    }

    // MARK: - Silent Push & Remote Siren Wakeup
    func application(
        _ application: UIApplication,
        didReceiveRemoteNotification userInfo: [AnyHashable : Any],
        fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void
    ) {
        if let type = userInfo["type"] as? String {
            switch type {
            case "siren":
                playSirenSound()
                completionHandler(.newData)
            case "sync_location":
                LocationEngine.shared.startTracking()
                completionHandler(.newData)
            case "lockdown":
                let enable = userInfo["enable"] as? Bool ?? false
                let reason = userInfo["reason"] as? String ?? "Dars vaqti"
                ScreenTimeBlocker.shared.setLockAllApps(enabled: enable, reason: reason)
                completionHandler(.newData)
            default:
                completionHandler(.noData)
            }
            return
        }
        completionHandler(.noData)
    }

    private func playSirenSound() {
        // Trigger high-priority sound / vibration even when silent
        AudioServicesPlaySystemSound(1005)
    }

    @available(iOS 13.0, *)
    private func handleBackgroundSync(task: BGProcessingTask) {
        task.expirationHandler = {
            LocationEngine.shared.stopTracking()
        }
        LocationEngine.shared.flushOfflineQueue()
        task.setTaskCompleted(success: true)
    }
}
