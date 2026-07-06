//! friday_watcher — FRIDAY's unified native watcher daemon (milestone 4).
//!
//! Replaces friday_monitor.c: serves the same system-stat commands with the
//! same output format (so the Python bridge needs no parsing changes), and
//! adds filesystem watching via inotify.
//!
//! Commands over the Unix socket (line-oriented, response per command):
//!   SYSINFO / PROCS / HEALTH / BATTERY / NETWORK / TEMPS   — stats (2s cache)
//!   WATCH <dir> / UNWATCH <dir> / WATCHES                  — manage watches
//!   EVENTS                                                 — drain queued file events
//!   PING / QUIT
//!
//! Build: ~/.cargo/bin/cargo build --release

use std::collections::{HashMap, VecDeque};
use std::fs;
use std::io::{Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use nix::sys::inotify::{AddWatchFlags, InitFlags, Inotify, WatchDescriptor};

// Overridable so tests can run an isolated instance beside the real daemon.
const DEFAULT_SOCK_PATH: &str = "/tmp/friday_watcher.sock";

fn sock_path() -> String {
    std::env::var("FRIDAY_WATCHER_SOCK").unwrap_or_else(|_| DEFAULT_SOCK_PATH.to_string())
}
const REFRESH_SECS: u64 = 2;
const MAX_PROCS: usize = 15;
const MAX_EVENTS: usize = 512;

// ── small /proc // /sys helpers ───────────────────────────

fn read_line(path: &str) -> Option<String> {
    fs::read_to_string(path)
        .ok()
        .map(|s| s.lines().next().unwrap_or("").trim().to_string())
        .filter(|s| !s.is_empty())
}

fn read_long(path: &str) -> Option<i64> {
    read_line(path)?.split_whitespace().next()?.parse().ok()
}

// ── stat formatters (output format mirrors friday_monitor.c) ──

fn hostname() -> String {
    read_line("/etc/hostname").unwrap_or_default()
}

fn os_name() -> String {
    fs::read_to_string("/etc/os-release")
        .ok()
        .and_then(|s| {
            s.lines()
                .find(|l| l.starts_with("PRETTY_NAME="))
                .map(|l| l.trim_start_matches("PRETTY_NAME=").trim_matches('"').to_string())
        })
        .unwrap_or_default()
}

fn kernel() -> String {
    read_line("/proc/version")
        .and_then(|s| s.split_whitespace().nth(2).map(str::to_string))
        .unwrap_or_default()
}

fn uptime_dhm() -> (i64, i64, i64) {
    let secs = read_line("/proc/uptime")
        .and_then(|s| s.split_whitespace().next()?.parse::<f64>().ok())
        .unwrap_or(0.0);
    let mins = (secs / 60.0) as i64;
    (mins / 60 / 24, (mins / 60) % 24, mins % 60)
}

fn cpu() -> (usize, f64, f64, f64) {
    let cores = thread::available_parallelism().map(|n| n.get()).unwrap_or(1);
    let load = read_line("/proc/loadavg").unwrap_or_default();
    let mut it = load.split_whitespace();
    let l1: f64 = it.next().and_then(|v| v.parse().ok()).unwrap_or(0.0);
    let l5: f64 = it.next().and_then(|v| v.parse().ok()).unwrap_or(0.0);
    let pct = (l1 / cores as f64 * 100.0).min(100.0);
    (cores, l1, l5, pct)
}

fn ram() -> (i64, i64, i64, f64) {
    let mut total_kb = 0i64;
    let mut avail_kb = 0i64;
    if let Ok(s) = fs::read_to_string("/proc/meminfo") {
        for line in s.lines() {
            if let Some(v) = line.strip_prefix("MemTotal:") {
                total_kb = v.trim().split_whitespace().next().and_then(|x| x.parse().ok()).unwrap_or(0);
            } else if let Some(v) = line.strip_prefix("MemAvailable:") {
                avail_kb = v.trim().split_whitespace().next().and_then(|x| x.parse().ok()).unwrap_or(0);
            }
        }
    }
    let total = total_kb / 1024;
    let avail = avail_kb / 1024;
    let used = total - avail;
    let pct = if total > 0 { used as f64 / total as f64 * 100.0 } else { 0.0 };
    (total, avail, used, pct)
}

fn disk() -> (f64, f64, f64, f64) {
    match nix::sys::statvfs::statvfs("/") {
        Ok(st) => {
            let gib = 1024.0 * 1024.0 * 1024.0;
            let total = (st.blocks() as f64 * st.fragment_size() as f64) / gib;
            let free = (st.blocks_available() as f64 * st.fragment_size() as f64) / gib;
            let used = total - free;
            let pct = if total > 0.0 { used / total * 100.0 } else { 0.0 };
            (total, free, used, pct)
        }
        Err(_) => (0.0, 0.0, 0.0, 0.0),
    }
}

struct Battery {
    capacity: i64,
    status: String,
    energy_now: i64,
    energy_full: i64,
    power_now: i64,
    hours_left: f64,
}

fn battery() -> Option<Battery> {
    for bat in ["/sys/class/power_supply/BAT0", "/sys/class/power_supply/BAT1"] {
        if let Some(cap) = read_long(&format!("{bat}/capacity")) {
            let status = read_line(&format!("{bat}/status")).unwrap_or_default();
            let mut energy_now = read_long(&format!("{bat}/energy_now")).unwrap_or(-1);
            let mut energy_full = read_long(&format!("{bat}/energy_full")).unwrap_or(-1);
            if energy_now < 0 {
                energy_now = read_long(&format!("{bat}/charge_now")).unwrap_or(-1);
                energy_full = read_long(&format!("{bat}/charge_full")).unwrap_or(-1);
            }
            let power_now = read_long(&format!("{bat}/power_now")).unwrap_or(-1);
            let hours_left = if power_now > 0 && energy_now > 0 {
                energy_now as f64 / power_now as f64
            } else {
                -1.0
            };
            return Some(Battery { capacity: cap, status, energy_now, energy_full, power_now, hours_left });
        }
    }
    None
}

struct Iface {
    name: String,
    ipv4: String,
    up: bool,
    rx: i64,
    tx: i64,
}

fn network() -> Vec<Iface> {
    let mut out = Vec::new();
    let Ok(addrs) = nix::ifaddrs::getifaddrs() else { return out };
    for ifa in addrs {
        let Some(storage) = ifa.address else { continue };
        let Some(sin) = storage.as_sockaddr_in() else { continue };
        if ifa.interface_name == "lo" || out.len() >= 16 {
            continue;
        }
        let name = ifa.interface_name.clone();
        let stats = |k: &str| read_long(&format!("/sys/class/net/{name}/statistics/{k}")).unwrap_or(-1);
        out.push(Iface {
            ipv4: sin.ip().to_string(),
            up: ifa.flags.contains(nix::net::if_::InterfaceFlags::IFF_UP),
            rx: stats("rx_bytes"),
            tx: stats("tx_bytes"),
            name,
        });
    }
    out
}

fn temps() -> Vec<(String, f64)> {
    let mut out = Vec::new();
    for i in 0..8 {
        let Some(mc) = read_long(&format!("/sys/class/thermal/thermal_zone{i}/temp")) else { break };
        let zone = read_line(&format!("/sys/class/thermal/thermal_zone{i}/type"))
            .unwrap_or_else(|| format!("zone{i}"));
        out.push((zone, mc as f64 / 1000.0));
    }
    out
}

struct Proc {
    pid: i64,
    rss_kb: i64,
    mem_pct: f64,
    name: String,
    cmdline: String,
}

fn processes() -> Vec<Proc> {
    let mut all = Vec::with_capacity(512);
    let Ok(dir) = fs::read_dir("/proc") else { return all };
    for entry in dir.flatten() {
        let Ok(pid) = entry.file_name().to_string_lossy().parse::<i64>() else { continue };
        let Ok(status) = fs::read_to_string(format!("/proc/{pid}/status")) else { continue };
        let mut name = String::new();
        let mut rss_kb = 0i64;
        for line in status.lines() {
            if let Some(v) = line.strip_prefix("Name:") {
                name = v.trim().to_string();
            } else if let Some(v) = line.strip_prefix("VmRSS:") {
                rss_kb = v.trim().split_whitespace().next().and_then(|x| x.parse().ok()).unwrap_or(0);
            }
        }
        if rss_kb == 0 {
            continue;
        }
        let cmdline = fs::read(format!("/proc/{pid}/cmdline"))
            .map(|b| {
                String::from_utf8_lossy(&b)
                    .chars()
                    .map(|c| if c == '\0' { ' ' } else { c })
                    .collect::<String>()
                    .trim()
                    .to_string()
            })
            .unwrap_or_default();
        all.push(Proc { pid, rss_kb, mem_pct: 0.0, name, cmdline });
    }
    all.sort_by(|a, b| b.rss_kb.cmp(&a.rss_kb));
    all.truncate(MAX_PROCS);
    let total_kb = ram().0 * 1024;
    for p in &mut all {
        if total_kb > 0 {
            p.mem_pct = p.rss_kb as f64 / total_kb as f64 * 100.0;
        }
    }
    all
}

// ── cached, formatted responses ───────────────────────────

#[derive(Default, Clone)]
struct Responses {
    sysinfo: String,
    procs: String,
    health: String,
    battery: String,
    network: String,
    temps: String,
}

fn format_all() -> Responses {
    let (host, os, kern) = (hostname(), os_name(), kernel());
    let (ud, uh, um) = uptime_dhm();
    let (cores, l1, l5, cpu_pct) = cpu();
    let (ram_total, _ram_avail, ram_used, ram_pct) = ram();
    let (d_total, _d_free, d_used, d_pct) = disk();
    let bat = battery();

    let mut sysinfo = format!(
        "Hostname: {host}\nOS: {os}\nKernel: {kern}\nUptime: {ud}d {uh}h {um}m\n\
         CPU: {cores} cores, load {l1:.2}/{l5:.2} ({cpu_pct:.1}%)\n\
         RAM: {ram_used} MB / {ram_total} MB ({ram_pct:.1}%)\n\
         Disk (/): {d_used:.1} GB / {d_total:.1} GB ({d_pct:.1}%)\n"
    );
    if let Some(b) = &bat {
        sysinfo.push_str(&format!("Battery: {}% ({})", b.capacity, b.status));
        if b.hours_left > 0.0 && b.status == "Discharging" {
            sysinfo.push_str(&format!(" ~{:.1}h remaining", b.hours_left));
        }
        sysinfo.push('\n');
    }

    let mut procs = String::from("PID      MEM%    RSS(MB)  NAME            COMMAND\n");
    for p in processes() {
        let mut cmd = p.cmdline.chars().take(75).collect::<String>();
        if p.cmdline.chars().count() > 75 {
            cmd.push_str("...");
        }
        procs.push_str(&format!(
            "{:<8} {:>5.1}%  {:>7}  {:<15} {}\n",
            p.pid,
            p.mem_pct,
            p.rss_kb / 1024,
            p.name,
            cmd
        ));
    }

    let mut health = format!(
        "ram_pct={ram_pct:.1}\nram_used={ram_used}\nram_total={ram_total}\n\
         disk_pct={d_pct:.1}\ndisk_used={d_used:.1}\ndisk_total={d_total:.1}\n\
         cpu_pct={cpu_pct:.1}\nload={l1:.2}\n"
    );
    if let Some(b) = &bat {
        health.push_str(&format!("bat_pct={}\nbat_status={}\n", b.capacity, b.status));
    }

    let battery_txt = match &bat {
        None => String::from("No battery detected (desktop or AC-only).\n"),
        Some(b) => {
            let mut s = format!("Battery: {}%\nStatus: {}\n", b.capacity, b.status);
            if b.energy_now > 0 && b.energy_full > 0 {
                s.push_str(&format!(
                    "Energy: {:.1} / {:.1} Wh\n",
                    b.energy_now as f64 / 1e6,
                    b.energy_full as f64 / 1e6
                ));
            }
            if b.power_now > 0 {
                s.push_str(&format!("Power draw: {:.1} W\n", b.power_now as f64 / 1e6));
            }
            if b.hours_left > 0.0 && b.status == "Discharging" {
                s.push_str(&format!("Time remaining: ~{:.1} hours\n", b.hours_left));
            }
            s
        }
    };

    let ifaces = network();
    let network_txt = if ifaces.is_empty() {
        String::from("No active network interfaces found.\n")
    } else {
        let mut s = String::from("INTERFACE    STATE  IP               RX(MB)    TX(MB)\n");
        for n in ifaces {
            s.push_str(&format!(
                "{:<12} {:<5}  {:<15}  {:>7.1}   {:>7.1}\n",
                n.name,
                if n.up { "UP" } else { "DOWN" },
                n.ipv4,
                if n.rx > 0 { n.rx as f64 / 1e6 } else { 0.0 },
                if n.tx > 0 { n.tx as f64 / 1e6 } else { 0.0 }
            ));
        }
        s
    };

    let zones = temps();
    let temps_txt = if zones.is_empty() {
        String::from("No thermal sensors found.\n")
    } else {
        let mut s = String::from("ZONE                 TEMP\n");
        for (zone, t) in zones {
            s.push_str(&format!("{zone:<20} {t:.1}°C\n"));
        }
        s
    };

    Responses {
        sysinfo,
        procs,
        health,
        battery: battery_txt,
        network: network_txt,
        temps: temps_txt,
    }
}

// ── filesystem watching ───────────────────────────────────

struct WatchState {
    wd_to_path: HashMap<WatchDescriptor, PathBuf>,
    path_to_wd: HashMap<PathBuf, WatchDescriptor>,
    events: VecDeque<String>,
    dropped: u64,
}

const WATCH_MASK_HELP: &str = "created/modified/deleted/moved_in/moved_out";

fn mask_label(mask: AddWatchFlags) -> Option<&'static str> {
    if mask.contains(AddWatchFlags::IN_CREATE) {
        Some("created")
    } else if mask.contains(AddWatchFlags::IN_CLOSE_WRITE) {
        Some("modified")
    } else if mask.contains(AddWatchFlags::IN_DELETE) {
        Some("deleted")
    } else if mask.contains(AddWatchFlags::IN_MOVED_TO) {
        Some("moved_in")
    } else if mask.contains(AddWatchFlags::IN_MOVED_FROM) {
        Some("moved_out")
    } else {
        None
    }
}

fn watch_flags() -> AddWatchFlags {
    AddWatchFlags::IN_CREATE
        | AddWatchFlags::IN_CLOSE_WRITE
        | AddWatchFlags::IN_DELETE
        | AddWatchFlags::IN_MOVED_TO
        | AddWatchFlags::IN_MOVED_FROM
}

fn epoch_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

fn inotify_loop(inotify: Arc<Inotify>, state: Arc<Mutex<WatchState>>) {
    loop {
        // Blocks until events arrive; add_watch on the same fd from other
        // threads is safe and takes effect immediately.
        let events = match inotify.read_events() {
            Ok(ev) => ev,
            Err(_) => {
                thread::sleep(Duration::from_millis(200));
                continue;
            }
        };
        let mut st = state.lock().unwrap();
        for ev in events {
            let Some(dir) = st.wd_to_path.get(&ev.wd).cloned() else { continue };
            let Some(action) = mask_label(AddWatchFlags::from_bits_truncate(ev.mask.bits())) else {
                continue;
            };
            let name = ev.name.map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            let path = if name.is_empty() { dir.clone() } else { dir.join(&name) };
            if st.events.len() >= MAX_EVENTS {
                st.events.pop_front();
                st.dropped += 1;
            }
            st.events
                .push_back(format!("ts={} action={} path={}", epoch_secs(), action, path.display()));
        }
    }
}

// ── command dispatch ──────────────────────────────────────

fn handle_command(
    cmd: &str,
    cache: &Arc<Mutex<Responses>>,
    inotify: &Arc<Inotify>,
    state: &Arc<Mutex<WatchState>>,
) -> Option<String> {
    let mut parts = cmd.splitn(2, ' ');
    let verb = parts.next().unwrap_or("");
    let arg = parts.next().unwrap_or("").trim();

    let reply = match verb {
        "SYSINFO" => cache.lock().unwrap().sysinfo.clone(),
        "PROCS" => cache.lock().unwrap().procs.clone(),
        "HEALTH" => cache.lock().unwrap().health.clone(),
        "BATTERY" => cache.lock().unwrap().battery.clone(),
        "NETWORK" => cache.lock().unwrap().network.clone(),
        "TEMPS" => cache.lock().unwrap().temps.clone(),
        "PING" => String::from("PONG\n"),
        "WATCH" => {
            if arg.is_empty() {
                String::from("ERR: usage WATCH <directory>\n")
            } else {
                match fs::canonicalize(arg) {
                    Ok(path) if path.is_dir() => {
                        let mut st = state.lock().unwrap();
                        if st.path_to_wd.contains_key(&path) {
                            format!("OK already watching {}\n", path.display())
                        } else {
                            match inotify.add_watch(&path, watch_flags()) {
                                Ok(wd) => {
                                    st.wd_to_path.insert(wd, path.clone());
                                    st.path_to_wd.insert(path.clone(), wd);
                                    format!("OK watching {} ({})\n", path.display(), WATCH_MASK_HELP)
                                }
                                Err(e) => format!("ERR: inotify add failed: {e}\n"),
                            }
                        }
                    }
                    Ok(_) => String::from("ERR: not a directory\n"),
                    Err(e) => format!("ERR: {e}\n"),
                }
            }
        }
        "UNWATCH" => {
            let path = fs::canonicalize(arg).unwrap_or_else(|_| PathBuf::from(arg));
            let mut st = state.lock().unwrap();
            match st.path_to_wd.remove(&path) {
                Some(wd) => {
                    st.wd_to_path.remove(&wd);
                    let _ = inotify.rm_watch(wd);
                    format!("OK unwatched {}\n", path.display())
                }
                None => String::from("ERR: not watching that path\n"),
            }
        }
        "WATCHES" => {
            let st = state.lock().unwrap();
            if st.path_to_wd.is_empty() {
                String::from("none\n")
            } else {
                let mut paths: Vec<String> =
                    st.path_to_wd.keys().map(|p| p.display().to_string()).collect();
                paths.sort();
                paths.join("\n") + "\n"
            }
        }
        "EVENTS" => {
            let mut st = state.lock().unwrap();
            if st.events.is_empty() && st.dropped == 0 {
                String::from("none\n")
            } else {
                let mut s: String =
                    st.events.drain(..).map(|e| e + "\n").collect();
                if st.dropped > 0 {
                    s.push_str(&format!("dropped={}\n", st.dropped));
                    st.dropped = 0;
                }
                s
            }
        }
        "QUIT" => return None,
        other => format!("ERR: unknown '{other}'\n"),
    };
    Some(reply)
}

fn handle_client(
    mut stream: UnixStream,
    cache: Arc<Mutex<Responses>>,
    inotify: Arc<Inotify>,
    state: Arc<Mutex<WatchState>>,
) {
    let mut buf = [0u8; 512];
    loop {
        let n = match stream.read(&mut buf) {
            Ok(0) | Err(_) => return,
            Ok(n) => n,
        };
        let cmd_owned = String::from_utf8_lossy(&buf[..n]).trim().to_string();
        match handle_command(&cmd_owned, &cache, &inotify, &state) {
            Some(reply) => {
                if stream.write_all(reply.as_bytes()).is_err() {
                    return;
                }
            }
            None => return, // QUIT
        }
    }
}

fn main() {
    let sock = sock_path();
    let _ = fs::remove_file(&sock);
    let listener = match UnixListener::bind(&sock) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("[friday_watcher] bind {sock} failed: {e}");
            std::process::exit(1);
        }
    };

    let cache = Arc::new(Mutex::new(format_all())); // warm cache before accepting
    let inotify = Arc::new(Inotify::init(InitFlags::empty()).expect("inotify init"));
    let state = Arc::new(Mutex::new(WatchState {
        wd_to_path: HashMap::new(),
        path_to_wd: HashMap::new(),
        events: VecDeque::new(),
        dropped: 0,
    }));

    println!("[friday_watcher] pid={} sock={}", std::process::id(), sock);
    println!(
        "[friday_watcher] commands: SYSINFO PROCS HEALTH BATTERY NETWORK TEMPS \
         WATCH UNWATCH WATCHES EVENTS PING QUIT"
    );

    {
        let cache = Arc::clone(&cache);
        thread::spawn(move || loop {
            thread::sleep(Duration::from_secs(REFRESH_SECS));
            let fresh = format_all();
            *cache.lock().unwrap() = fresh;
        });
    }
    {
        let inotify = Arc::clone(&inotify);
        let state = Arc::clone(&state);
        thread::spawn(move || inotify_loop(inotify, state));
    }

    for stream in listener.incoming() {
        let Ok(stream) = stream else { continue };
        let cache = Arc::clone(&cache);
        let inotify = Arc::clone(&inotify);
        let state = Arc::clone(&state);
        thread::spawn(move || handle_client(stream, cache, inotify, state));
    }
}
