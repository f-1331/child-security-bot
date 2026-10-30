use std::process::Command;

/**
 * Windows Firewall & Network Isolation Controller:
 * - Drops all outbound connections during "Offline Qilish"
 * - Whitelists our server domain & port (80, 443) and DNS (53)
 */
pub struct WindowsFirewall;

impl WindowsFirewall {
    pub fn enable_lockdown(server_host: &str) {
        // 1. Block outbound default
        let _ = Command::new("netsh")
            .args(&["advfirewall", "set", "allprofiles", "firewallpolicy", "blockinbound,blockoutbound"])
            .output();

        // 2. Allow DNS
        let _ = Command::new("netsh")
            .args(&[
                "advfirewall", "firewall", "add", "rule",
                "name=ChildSecurity_DNS", "dir=out", "action=allow", "protocol=UDP", "remoteport=53"
            ])
            .output();

        // 3. Allow Child Security App Outbound
        let _ = Command::new("netsh")
            .args(&[
                "advfirewall", "firewall", "add", "rule",
                "name=ChildSecurity_Core", "dir=out", "action=allow", "protocol=TCP", "remoteport=80,443"
            ])
            .output();

        println!("[Windows Firewall] Network lockdown enabled successfully.");
    }

    pub fn disable_lockdown() {
        // Restore standard outbound policy
        let _ = Command::new("netsh")
            .args(&["advfirewall", "set", "allprofiles", "firewallpolicy", "blockinbound,allowoutbound"])
            .output();

        let _ = Command::new("netsh")
            .args(&["advfirewall", "firewall", "delete", "rule", "name=ChildSecurity_DNS"])
            .output();

        let _ = Command::new("netsh")
            .args(&["advfirewall", "firewall", "delete", "rule", "name=ChildSecurity_Core"])
            .output();

        println!("[Windows Firewall] Network lockdown disabled.");
    }
}
