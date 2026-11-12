// Fabric Conditioning menu-bar app. It registers the boot service (the database, the web app and the
// outside link run under launchd, without anyone logged in) and shows their status.
import Cocoa
import ServiceManagement

let base = URL(string: "http://127.0.0.1:8120")!
let daemonPlist = "org.egr.fabric-conditioning.plist"
let agentLabel = "org.egr.fabric-conditioning.login"

struct Summary: Decodable {
    struct Update: Decodable { let latest: String?; let available: Bool; let in_progress: Bool }
    struct Station: Decodable { let name: String; let status: String }
    let version: String
    let update: Update
    let outside_url: String?
    let stations: [Station]
    let printer: String?
}

final class App: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    var summary: Summary?
    var healthy = false
    var serviceNote: String?
    var openAtStart = true

    func applicationWillFinishLaunching(_ notification: Notification) {
        // Started as a login item: stay in the menu bar. Opened by a person: also open the staff app.
        if let event = NSAppleEventManager.shared().currentAppleEvent,
           event.paramDescriptor(forKeyword: keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem {
            openAtStart = false
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        menu.delegate = self
        item.menu = menu
        paint()
        registerServices()
        refresh()
        Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            self?.registerServices()
            self?.refresh()
        }
        if openAtStart { waitThenOpen(tries: 60) }
    }

    // MARK: boot service

    func registerServices() {
        try? SMAppService.mainApp.register()
        let daemon = SMAppService.daemon(plistName: daemonPlist)
        if daemon.status == .notRegistered || daemon.status == .notFound {
            do { try daemon.register() } catch { NSLog("Boot service: %@", error.localizedDescription) }
        }
        switch daemon.status {
        case .enabled:
            serviceNote = nil
            removeLoginFallback()
        case .requiresApproval:
            serviceNote = "Allow in Login Items to start at boot"
            installLoginFallback()
        default:
            serviceNote = "Starts at login only"
            installLoginFallback()
        }
    }

    var fallbackPlist: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/LaunchAgents/\(agentLabel).plist")
    }

    // Until the boot service is approved, run the same service when this account logs in.
    func installLoginFallback() {
        guard !FileManager.default.fileExists(atPath: fallbackPlist.path),
              let source = Bundle.main.url(forResource: "login-service", withExtension: "plist") else { return }
        try? FileManager.default.createDirectory(at: fallbackPlist.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard (try? FileManager.default.copyItem(at: source, to: fallbackPlist)) != nil else { return }
        launchctl(["bootstrap", "gui/\(getuid())", fallbackPlist.path])
    }

    func removeLoginFallback() {
        guard FileManager.default.fileExists(atPath: fallbackPlist.path) else { return }
        launchctl(["bootout", "gui/\(getuid())/\(agentLabel)"])
        try? FileManager.default.removeItem(at: fallbackPlist)
    }

    func launchctl(_ arguments: [String]) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        try? process.run()
        process.waitUntilExit()
    }

    // MARK: status

    func refresh() {
        get("/api/health") { [weak self] data in
            self?.healthy = data != nil
            self?.paint()
        }
        get("/api/host/summary") { [weak self] data in
            self?.summary = data.flatMap { try? JSONDecoder().decode(Summary.self, from: $0) }
            self?.paint()
        }
    }

    func get(_ path: String, method: String = "GET", done: @escaping (Data?) -> Void) {
        var request = URLRequest(url: base.appendingPathComponent(path), timeoutInterval: 5)
        request.httpMethod = method
        if method == "POST" {
            request.httpBody = "{}".data(using: .utf8)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        URLSession.shared.dataTask(with: request) { data, response, _ in
            let ok = ((response as? HTTPURLResponse)?.statusCode ?? 500) < 300
            DispatchQueue.main.async { done(ok ? data : nil) }
        }.resume()
    }

    func paint() {
        // A station that never connected isn't set up yet; only lost or scanner-less ones need someone.
        let warn = serviceNote != nil || summary?.stations.contains { ["offline", "no_scanner"].contains($0.status) } == true
        let color: NSColor = !healthy ? .systemRed : warn ? .systemOrange : .systemGreen
        let image = NSImage(systemSymbolName: "circle.fill", accessibilityDescription: "Fabric Conditioning")?
            .withSymbolConfiguration(.init(pointSize: 11, weight: .regular).applying(.init(paletteColors: [color])))
        item.button?.image = image
        item.button?.toolTip = healthy ? "Fabric Conditioning is running" : "Fabric Conditioning is not running"
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let state = healthy ? "Running" + (summary.map { " · \($0.version)" } ?? "") : "Not running"
        menu.addItem(withTitle: state, action: nil, keyEquivalent: "").isEnabled = false
        if let note = serviceNote { add(menu, note, #selector(openLoginItems)) }
        add(menu, "Open Fabric Conditioning", #selector(openStaff), key: "o")
        if let summary {
            if let link = summary.outside_url {
                menu.addItem(.separator())
                menu.addItem(withTitle: link, action: nil, keyEquivalent: "").isEnabled = false
                add(menu, "Copy outside link", #selector(copyLink))
            }
            if !summary.stations.isEmpty {
                menu.addItem(.separator())
                for station in summary.stations {
                    let label = ["ready": "Ready", "no_scanner": "No scanner", "offline": "Offline"][station.status] ?? "Not connected"
                    menu.addItem(withTitle: "\(station.name): \(label)", action: nil, keyEquivalent: "").isEnabled = false
                }
            }
            if let printer = summary.printer {
                menu.addItem(withTitle: "Printer: \(printer)", action: nil, keyEquivalent: "").isEnabled = false
            }
            menu.addItem(.separator())
            if summary.update.in_progress {
                menu.addItem(withTitle: "Updating…", action: nil, keyEquivalent: "").isEnabled = false
            } else if summary.update.available, let latest = summary.update.latest {
                add(menu, "Update to \(latest)", #selector(startUpdate))
            } else {
                add(menu, "Check for updates", #selector(checkUpdates))
            }
        }
        add(menu, "System health", #selector(openHealth))
        menu.addItem(.separator())
        add(menu, "Hide menu icon", #selector(quit))
    }

    func add(_ menu: NSMenu, _ title: String, _ action: Selector, key: String = "") {
        let entry = NSMenuItem(title: title, action: action, keyEquivalent: key)
        entry.target = self
        menu.addItem(entry)
    }

    // MARK: actions

    func waitThenOpen(tries: Int) {
        get("/api/health") { [weak self] data in
            if data != nil || tries <= 0 { self?.openStaff() }
            else { DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self?.waitThenOpen(tries: tries - 1) } }
        }
    }

    @objc func openStaff() { NSWorkspace.shared.open(base) }
    @objc func openHealth() { NSWorkspace.shared.open(URL(string: base.absoluteString + "/#health")!) }
    @objc func openLoginItems() { SMAppService.openSystemSettingsLoginItems() }
    @objc func quit() { NSApp.terminate(nil) }

    @objc func copyLink() {
        guard let link = summary?.outside_url else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(link, forType: .string)
    }

    @objc func checkUpdates() {
        get("/api/system/update/check", method: "POST") { [weak self] _ in self?.refresh() }
    }

    @objc func startUpdate() {
        let alert = NSAlert()
        alert.messageText = "Update to \(summary?.update.latest ?? "the new version")?"
        alert.informativeText = "The app stops for about a minute. A backup is made first."
        alert.addButton(withTitle: "Update")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        get("/api/system/update", method: "POST") { [weak self] _ in self?.refresh() }
    }
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
