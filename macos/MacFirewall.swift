import Foundation

/**
 * macOS Local Firewall Controller (pfctl):
 * - Blocks outbound internet for games & browsers during "Offline Qilish"
 * - Whitelists our Child Security API server and DNS
 */
public final class MacFirewall {

    public static let shared = MacFirewall()
    private init() {}

    public func enableLockdown(serverHost: String = "child-security-site.onrender.com") {
        let rulePath = "/tmp/child_security_pf.conf"
        let rules = """
        # Child Security Lockdown Rules
        set block-policy drop
        block out all
        pass out proto udp from any to any port 53
        pass out proto tcp from any to any port 53
        pass out proto tcp from any to \(serverHost) port {80, 443}
        """

        try? rules.write(toFile: rulePath, atomically: true, encoding: .utf8)

        // Execute pfctl command
        let script = "pfctl -e -f \(rulePath) 2>/dev/null"
        executeShell(script)
        NSLog("[MacFirewall] Network lockdown enabled via pfctl")
    }

    public func disableLockdown() {
        let script = "pfctl -d 2>/dev/null"
        executeShell(script)
        NSLog("[MacFirewall] Network lockdown disabled")
    }

    private func executeShell(_ command: String) {
        let task = Process()
        task.launchPath = "/bin/sh"
        task.arguments = ["-c", command]
        try? task.run()
    }
}
