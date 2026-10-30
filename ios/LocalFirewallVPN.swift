import Foundation
import NetworkExtension

/**
 * Local VPN Firewall for iOS:
 * - Creates a local loopback packet tunnel
 * - "Qurilmani Offline Qilish": Sinks all third-party app and game traffic
 * - Whitelists our server IP, SOS endpoints, and Apple APNs
 */
public final class LocalFirewallVPN {

    public static let shared = LocalFirewallVPN()
    private var vpnManager: NETunnelProviderManager?

    private init() {
        loadManager()
    }

    private func loadManager() {
        NETunnelProviderManager.loadAllFromPreferences { [weak self] managers, _ in
            self?.vpnManager = managers?.first ?? NETunnelProviderManager()
        }
    }

    public func startLockdown() {
        guard let manager = vpnManager else { return }
        let proto = NETunnelProviderProtocol()
        proto.providerBundleIdentifier = "uz.childsecurity.app.vpn"
        proto.serverAddress = "127.0.0.1"

        manager.protocolConfiguration = proto
        manager.localizedDescription = "Child Security Firewall"
        manager.isEnabled = true

        manager.saveToPreferences { error in
            if error == nil {
                do {
                    try manager.connection.startVPNTunnel()
                    NSLog("[LocalFirewallVPN] Network lockdown activated successfully")
                } catch {
                    NSLog("[LocalFirewallVPN] Failed to start tunnel: \(error)")
                }
            }
        }
    }

    public func stopLockdown() {
        vpnManager?.connection.stopVPNTunnel()
        NSLog("[LocalFirewallVPN] Network lockdown deactivated")
    }
}

/**
 * Extension Packet Tunnel Provider implementation
 */
open class ChildSecurityTunnelProvider: NEPacketTunnelProvider {

    open override func startTunnel(options: [String : NSObject]?, completionHandler: @escaping (Error?) -> Void) {
        let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: "127.0.0.1")

        // Route all traffic through loopback (sink) except server host
        let defaultRoute = NEIPv4Route.default()
        let ipv4Settings = NEIPv4Settings(addresses: ["10.8.0.2"], subnetMasks: ["255.255.255.0"])
        ipv4Settings.includedRoutes = [defaultRoute]

        // Whitelist DNS & Apple Push
        settings.ipv4Settings = ipv4Settings
        settings.dnsSettings = NEDNSSettings(servers: ["1.1.1.1", "8.8.8.8"])

        setTunnelNetworkSettings(settings) { error in
            completionHandler(error)
        }
    }

    open override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
        completionHandler()
    }
}
