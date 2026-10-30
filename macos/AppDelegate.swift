import Cocoa
import WebKit
import CoreLocation
import CoreWLAN
import IOKit.ps
import AVFoundation

@main
class AppDelegate: NSObject, NSApplicationDelegate, CLLocationManagerDelegate, WKScriptMessageHandler {

    private var statusItem: NSStatusItem!
    private var window: NSWindow!
    private var webView: WKWebView!
    private let locationManager = CLLocationManager()
    private var audioRecorder: AVAudioRecorder?

    private var childId: String {
        UserDefaults.standard.string(forKey: "child_id") ?? ""
    }
    private var serverUrl: String {
        UserDefaults.standard.string(forKey: "server_url") ?? "https://child-security-site.onrender.com"
    }

    func applicationDidFinishLaunching(_ aNotification: Notification) {
        // 1. Setup Menu Bar Icon
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.title = "🛡️"
            button.action = #selector(toggleWindow)
        }

        // 2. Setup WebView Window
        setupWindow()

        // 3. Start Location & Process Monitoring
        locationManager.delegate = self
        locationManager.desiredAccuracy = kCLLocationAccuracyBest
        locationManager.startUpdatingLocation()

        MacProcessBlocker.shared.startMonitoring()

        // 4. Heartbeat telemetry timer (every 30s)
        Timer.scheduledTimer(withTimeInterval: 30.0, repeats: true) { [weak self] _ in
            self?.sendMacTelemetry()
        }
    }

    private func setupWindow() {
        let config = WKWebViewConfiguration()
        let controller = WKUserContentController()
        controller.add(self, name: "NativeBridge")
        config.userContentController = controller

        let frame = NSRect(x: 0, y: 0, width: 420, height: 720)
        window = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Child Security Guardian"
        window.isReleasedWhenClosed = false

        webView = WKWebView(frame: frame, configuration: config)
        window.contentView = webView

        let defaultUrl = URL(string: "\(serverUrl)/?role=child")!
        webView.load(URLRequest(url: defaultUrl))
    }

    @objc private func toggleWindow() {
        if window.isVisible {
            window.orderOut(nil)
        } else {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    // MARK: - Battery & Wi-Fi Telemetry
    private func getMacBatteryLevel() -> (Int, Bool) {
        guard let snapshot = IOPSCopyPowerSourcesInfo()?.takeRetainedValue(),
              let sources = IOPSCopyPowerSourcesList(snapshot)?.takeRetainedValue() as? [CFTypeRef] else {
            return (100, true)
        }
        for ps in sources {
            if let desc = IOPSGetPowerSourceDescription(snapshot, ps)?.takeUnretainedValue() as? [String: Any] {
                let cur = desc[kIOPSCurrentCapacityKey] as? Int ?? 100
                let max = desc[kIOPSMaxCapacityKey] as? Int ?? 100
                let isCharging = (desc[kIOPSIsChargingKey] as? Bool) ?? false
                let percent = Int((Double(cur) / Double(max)) * 100.0)
                return (percent, isCharging)
            }
        }
        return (100, true)
    }

    private func getConnectedWifiSsid() -> String? {
        return CWWiFiClient.shared().interface()?.ssid()
    }

    private func sendMacTelemetry() {
        guard !childId.isEmpty else { return }
        let (batt, charging) = getMacBatteryLevel()
        let loc = locationManager.location?.coordinate

        var payload: [String: Any] = [
            "latitude": loc?.latitude ?? 0.0,
            "longitude": loc?.longitude ?? 0.0,
            "accuracy": locationManager.location?.horizontalAccuracy ?? 50.0,
            "speed": 0,
            "speed_kmh": 0,
            "battery_level": batt,
            "is_charging": charging,
            "sound_mode": "normal",
            "is_sos_active": false
        ]

        if let ssid = getConnectedWifiSsid() {
            payload["wifi_ssid"] = ssid
        }

        guard let jsonData = try? JSONSerialization.data(withJSONObject: payload) else { return }
        let cleanBase = serverUrl.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: "\(cleanBase)/api/v1/webapp/child/\(childId)/location") else { return }

        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = jsonData
        URLSession.shared.dataTask(with: req).resume()
    }

    // MARK: - WKScriptMessageHandler
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "NativeBridge", let dict = message.body as? [String: Any] else { return }
        let type = dict["type"] as? String ?? ""

        switch type {
        case "lock_apps":
            let enable = dict["enable"] as? Bool ?? false
            let reason = dict["reason"] as? String ?? "Dars vaqti"
            MacProcessBlocker.shared.setLockdown(enabled: enable, reason: reason)
            if enable {
                MacFirewall.shared.enableLockdown()
            } else {
                MacFirewall.shared.disableLockdown()
            }

        case "record_5s_audio":
            record5sAudio()

        default:
            break
        }
    }

    private func record5sAudio() {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let url = docs.appendingPathComponent("mac_sos_audio_\(Date().timeIntervalSince1970).m4a")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 16000.0,
            AVNumberOfChannelsKey: 1,
            AVEncoderBitRateKey: 16000
        ]
        audioRecorder = try? AVAudioRecorder(url: url, settings: settings)
        audioRecorder?.record(forDuration: 5.0)
    }
}
