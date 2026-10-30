import Foundation
import UIKit
import WebKit
import AVFoundation

/**
 * Native WKWebView Controller & Bi-Directional Bridge:
 * - Hosts webapp/ inside iOS App
 * - Listens for window.webkit.messageHandlers.NativeBridge.postMessage(...)
 * - Injects device battery, pedometer steps, and 5s audio snapshot
 */
public final class WebBridgeViewController: UIViewController, WKScriptMessageHandler, WKNavigationDelegate, AVAudioRecorderDelegate {

    private var webView: WKWebView!
    private var audioRecorder: AVAudioRecorder?

    override public func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(red: 0.04, green: 0.05, blue: 0.09, alpha: 1.0)
        setupWebView()
        loadWebContent()
    }

    private func setupWebView() {
        let config = WKWebViewConfiguration()
        let controller = WKUserContentController()

        // Register Native Bridge handler
        controller.add(self, name: "NativeBridge")
        config.userContentController = controller
        config.allowsInlineMediaPlayback = true

        webView = WKWebView(frame: view.bounds, configuration: config)
        webView.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        webView.navigationDelegate = self
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.bounces = false
        view.addSubview(webView)
    }

    private func loadWebContent() {
        // 1. Try local bundled webapp/index.html
        if let localUrl = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "webapp") {
            webView.loadFileURL(localUrl, allowingReadAccessTo: localUrl.deletingLastPathComponent())
            return
        }

        // 2. Fallback to production server URL
        let defaultUrl = UserDefaults.standard.string(forKey: "server_url") ?? "https://child-security-site.onrender.com"
        if let url = URL(string: defaultUrl) {
            webView.load(URLRequest(url: url))
        }
    }

    // MARK: - WKScriptMessageHandler
    public func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "NativeBridge", let dict = message.body as? [String: Any] else { return }
        let type = dict["type"] as? String ?? ""

        switch type {
        case "telemetry":
            if let lat = dict["latitude"] as? Double, let lon = dict["longitude"] as? Double {
                NSLog("[WebBridge iOS] Telemetry received from Web: (\(lat), \(lon))")
            }

        case "haptic":
            let style = dict["style"] as? String ?? "light"
            triggerHaptic(style: style)

        case "sos_trigger":
            handleSOS()

        case "record_5s_audio":
            recordAndUploadAudioSnapshot()

        case "lock_apps":
            let enable = dict["enable"] as? Bool ?? false
            let reason = dict["reason"] as? String ?? "Dars vaqti"
            ScreenTimeBlocker.shared.setLockAllApps(enabled: enable, reason: reason)

        case "get_device_info":
            provideDeviceInfo()

        default:
            break
        }
    }

    private func triggerHaptic(style: String) {
        DispatchQueue.main.async {
            switch style {
            case "heavy":
                UIImpactFeedbackGenerator(style: .heavy).impactOccurred()
            case "medium":
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            case "success":
                UINotificationFeedbackGenerator().notificationOccurred(.success)
            case "warning":
                UINotificationFeedbackGenerator().notificationOccurred(.warning)
            default:
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
            }
        }
    }

    private func handleSOS() {
        triggerHaptic(style: "heavy")
        recordAndUploadAudioSnapshot()
    }

    // MARK: - 5s Audio Snapshot (16kbps Opus/M4A)
    private func recordAndUploadAudioSnapshot() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
            try session.setActive(true)

            let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            let audioUrl = docs.appendingPathComponent("sos_audio_\(Date().timeIntervalSince1970).m4a")

            let settings: [String: Any] = [
                AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
                AVSampleRateKey: 16000.0,
                AVNumberOfChannelsKey: 1,
                AVEncoderBitRateKey: 16000 // 16kbps ultra-lightweight compression
            ]

            audioRecorder = try AVAudioRecorder(url: audioUrl, settings: settings)
            audioRecorder?.delegate = self
            audioRecorder?.record(forDuration: 5.0)
            NSLog("[WebBridge iOS] Recording 5-second audio snapshot...")
        } catch {
            NSLog("[WebBridge iOS] Audio record error: \(error)")
        }
    }

    public func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        guard flag else { return }
        NSLog("[WebBridge iOS] 5s audio recorded successfully at \(recorder.url)")
        // Inject completion into JS
        let js = "if(window.onNativeAudioRecorded) { window.onNativeAudioRecorded(); }"
        DispatchQueue.main.async { [weak self] in
            self?.webView.evaluateJavaScript(js, completionHandler: nil)
        }
    }

    private func provideDeviceInfo() {
        UIDevice.current.isBatteryMonitoringEnabled = true
        let batt = max(0, Int(round(UIDevice.current.batteryLevel * 100)))
        let charging = UIDevice.current.batteryState == .charging || UIDevice.current.batteryState == .full
        let steps = UserDefaults.standard.integer(forKey: "today_steps")

        let js = "if(window.onNativeDeviceInfo) { window.onNativeDeviceInfo({ battery: \(batt), isCharging: \(charging), steps: \(steps) }); }"
        DispatchQueue.main.async { [weak self] in
            self?.webView.evaluateJavaScript(js, completionHandler: nil)
        }
    }
}
