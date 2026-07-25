// SPDX-License-Identifier: AGPL-3.0-or-later

//! End-to-end acceptance for the repo-baked DM3 mega patch.
//!
//! The test deliberately creates a new server tree and never sends `Unlink` or `PlanLink`. Required
//! inputs are explicit because the Quake server binary and copyrighted game data do not live in this
//! repository:
//!
//! ```text
//! RTX_DM3_FRESH_BOOT_MVDSV=/path/to/mvdsv
//! RTX_DM3_FRESH_BOOT_PAK0=/path/to/pak0.pak
//! RTX_DM3_FRESH_BOOT_BSP=/path/to/dm3.bsp
//! RTX_DM3_FRESH_BOOT_QWPROGS=/path/to/qwprogs.so
//! cargo test -p rtx-game --test dm3_mega_fresh_boot -- --ignored --nocapture
//! ```

use std::collections::{BTreeMap, VecDeque};
use std::fs::{self, File};
use std::io::Write;
use std::net::{TcpListener, TcpStream, UdpSocket};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use rtx_ctlproto::{Cmd, Event, Msg, Request, Resp, SngMegaResult, SngMegaScenario, StatusResp};
use serde::Deserialize;

const PATCH_PATH: &str = "../rtx-nav/data/navpatches/dm3-mega-v1.json";
const BUILD_TIMEOUT: Duration = Duration::from_secs(12 * 60);

#[derive(Debug, Deserialize)]
struct PatchContract {
    id: String,
    source_graph_sha256: String,
    patched_graph_sha256: String,
    counts: Counts,
    verification: Verification,
}

#[derive(Debug, Deserialize)]
struct Counts {
    patched_links: u32,
    patched_active_links: u32,
}

#[derive(Debug, Deserialize)]
struct Verification {
    attempts: u32,
    minimum_successes: u32,
    max_secs: f32,
    mega: [f32; 3],
    rockets: [f32; 3],
    scenarios: Vec<Scenario>,
}

#[derive(Debug, Deserialize)]
struct Scenario {
    name: String,
    wire: String,
    start: [f32; 3],
}

struct Server {
    child: Child,
    root: PathBuf,
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

struct Control {
    stream: TcpStream,
    next_id: i64,
    events: VecDeque<Event>,
}

impl Control {
    fn connect(port: u16, deadline: Instant) -> Self {
        loop {
            match TcpStream::connect(("127.0.0.1", port)) {
                Ok(stream) => {
                    stream
                        .set_read_timeout(Some(Duration::from_secs(2)))
                        .expect("set control read timeout");
                    stream.set_nodelay(true).expect("set TCP_NODELAY");
                    return Self {
                        stream,
                        next_id: 1,
                        events: VecDeque::new(),
                    };
                }
                Err(error) if Instant::now() < deadline => {
                    let _ = error;
                    thread::sleep(Duration::from_millis(100));
                }
                Err(error) => panic!("control port did not open: {error}"),
            }
        }
    }

    fn read(&mut self, deadline: Instant) -> Option<Msg> {
        while Instant::now() < deadline {
            match rtx_ctlproto::read_frame(&mut self.stream) {
                Ok(Some(frame)) => return Some(rtx_ctlproto::decode(&frame).expect("decode control frame")),
                Ok(None) => panic!("control connection closed"),
                Err(error)
                    if matches!(
                        error.kind(),
                        std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock
                    ) => {}
                Err(error) => panic!("read control frame: {error}"),
            }
        }
        None
    }

    fn request(&mut self, cmd: Cmd, timeout: Duration) -> (i64, Resp) {
        let id = self.next_id;
        self.next_id += 1;
        self.stream
            .write_all(&rtx_ctlproto::to_frame(&Request { id, cmd }))
            .expect("write control request");
        let deadline = Instant::now() + timeout;
        while let Some(message) = self.read(deadline) {
            match message {
                Msg::Reply { id: reply_id, result } if reply_id == id => {
                    return (id, result.unwrap_or_else(|error| panic!("control error: {error}")))
                }
                Msg::Event(event) => self.events.push_back(event),
                Msg::Reply { .. } => {}
            }
        }
        panic!("control request {id} timed out");
    }

    fn status(&mut self) -> StatusResp {
        match self.request(Cmd::Status, Duration::from_secs(10)).1 {
            Resp::Status(status) => *status,
            response => panic!("Status returned {response:?}"),
        }
    }

    fn wait_sng_result(&mut self, request_id: i64, timeout: Duration) -> SngMegaResult {
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(index) = self
                .events
                .iter()
                .position(|event| matches!(event, Event::SngMegaResult(result) if result.request_id == request_id))
            {
                let event = self.events.remove(index).expect("queued event exists");
                if let Event::SngMegaResult(result) = event {
                    return *result;
                }
                unreachable!();
            }
            match self.read(deadline) {
                Some(Msg::Event(Event::SngMegaResult(result))) if result.request_id == request_id => {
                    return *result;
                }
                Some(Msg::Event(event)) => self.events.push_back(event),
                Some(Msg::Reply { .. }) => {}
                None => panic!("SngMega result {request_id} timed out"),
            }
        }
    }
}

fn required_path(name: &str) -> PathBuf {
    let value = std::env::var_os(name).unwrap_or_else(|| panic!("{name} must point at a test asset"));
    let path = PathBuf::from(value);
    assert!(path.is_file(), "{name} is not a file: {}", path.display());
    path
}

fn free_tcp_port() -> u16 {
    TcpListener::bind(("127.0.0.1", 0))
        .expect("bind ephemeral TCP port")
        .local_addr()
        .expect("TCP local address")
        .port()
}

fn free_udp_port() -> u16 {
    UdpSocket::bind(("127.0.0.1", 0))
        .expect("bind ephemeral UDP port")
        .local_addr()
        .expect("UDP local address")
        .port()
}

fn copy(source: &Path, target: &Path) {
    fs::copy(source, target)
        .unwrap_or_else(|error| panic!("copy {} -> {}: {error}", source.display(), target.display()));
}

fn fresh_root() -> PathBuf {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock after epoch")
        .as_nanos();
    std::env::temp_dir().join(format!("rtx-dm3-mega-fresh-{}-{stamp}", std::process::id()))
}

fn spawn_server(control_port: u16, game_port: u16, qtv_port: u16) -> Server {
    let mvdsv = required_path("RTX_DM3_FRESH_BOOT_MVDSV");
    let pak0 = required_path("RTX_DM3_FRESH_BOOT_PAK0");
    let bsp = required_path("RTX_DM3_FRESH_BOOT_BSP");
    let qwprogs = required_path("RTX_DM3_FRESH_BOOT_QWPROGS");
    let root = fresh_root();
    fs::create_dir_all(root.join("id1")).expect("create fresh id1");
    fs::create_dir_all(root.join("qw/maps")).expect("create fresh qw/maps");
    copy(&mvdsv, &root.join("mvdsv"));
    copy(&pak0, &root.join("id1/pak0.pak"));
    copy(&bsp, &root.join("qw/maps/dm3.bsp"));
    copy(&qwprogs, &root.join("qw/qwprogs.so"));

    let config = format!(
        r#"hostname "RTX DM3 mega fresh boot"
sv_progtype 1
deathmatch 1
timelimit 0
fraglimit 0
maxclients 8
maxspectators 0
set rtx_mode dm
set rtx_match ""
set rtx_grapple 0
set rtx_doublejump 0
set rtx_walljump 0
set rtx_elevator_jump 0
set rtx_shootable_grenades 0
set rtx_bot_bhop 1
set rtx_bot_curljump 1
set rtx_jump_curl_gain 0
set rtx_bot_rocketjump 1
set rtx_rj_cost_scale 0.35
set rtx_bot_count 1
set rtx_bot_name "dm3-fresh"
set rtx_bot_alone 1
set rtx_bot_pacifist 1
set rtx_bot_skill 7
qtv_maxstreams 0
qtv_streamport {qtv_port}
set rtx_control_port {control_port}
set developer 1
map dm3
"#
    );
    fs::write(root.join("qw/freshboot.cfg"), config).expect("write fresh server config");
    let log = File::create(root.join("server.log")).expect("create server log");
    let child = Command::new(root.join("mvdsv"))
        .current_dir(&root)
        .args(["-port", &game_port.to_string(), "+exec", "freshboot.cfg"])
        .stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone().expect("clone server log")))
        .stderr(Stdio::from(log))
        .spawn()
        .expect("spawn fresh mvdsv");
    eprintln!("fresh runtime: {}", root.display());
    Server { child, root }
}

fn scenario_kind(wire: &str) -> SngMegaScenario {
    match wire {
        "West" => SngMegaScenario::West,
        "South" => SngMegaScenario::South,
        value => panic!("unsupported SngMega scenario {value}"),
    }
}

fn assert_zero_cvar(control: &mut Control, name: &str) {
    match control
        .request(Cmd::Get { name: name.to_string() }, Duration::from_secs(5))
        .1
    {
        Resp::Get { value, .. } => assert_eq!(value, 0.0, "{name} must stay disabled"),
        response => panic!("Get {name} returned {response:?}"),
    }
}

#[test]
#[ignore = "requires mvdsv, pak0.pak, dm3.bsp, and a freshly-built qwprogs module"]
fn dm3_mega_patch_survives_fresh_boot_and_40_item_trials() {
    let manifest_path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(PATCH_PATH);
    let contract: PatchContract =
        serde_json::from_slice(&fs::read(&manifest_path).expect("read DM3 patch")).expect("parse DM3 patch");
    assert_eq!(contract.verification.attempts, 40);
    assert_eq!(contract.verification.scenarios.len(), 2);

    let control_port = free_tcp_port();
    let game_port = free_udp_port();
    let qtv_port = free_tcp_port();
    let server = spawn_server(control_port, game_port, qtv_port);
    let mut control = Control::connect(control_port, Instant::now() + Duration::from_secs(20));
    let build_deadline = Instant::now() + BUILD_TIMEOUT;
    let ready = loop {
        assert!(Instant::now() < build_deadline, "navmesh build timed out");
        let status = control.status();
        if let Some(error) = status.nav_patch_error.as_ref() {
            panic!("built-in patch failed closed: {error}");
        }
        if status.navmesh == "ready" && !status.bots.is_empty() {
            break status;
        }
        thread::sleep(Duration::from_secs(1));
    };

    let patch = ready
        .nav_patch
        .as_ref()
        .expect("DM3 status has built-in patch provenance");
    assert_eq!(patch.id, contract.id);
    assert_eq!(patch.source_graph_sha256, contract.source_graph_sha256);
    assert_eq!(patch.patched_graph_sha256, contract.patched_graph_sha256);
    assert_eq!(patch.total_links, contract.counts.patched_links);
    assert_eq!(patch.active_links, contract.counts.patched_active_links);
    assert_eq!(ready.links, contract.counts.patched_links);
    assert_zero_cvar(&mut control, "rtx_walljump");
    assert_zero_cvar(&mut control, "rtx_doublejump");

    let bot = ready.bots[0].ent;
    let mut successes = 0u32;
    let mut by_scenario = BTreeMap::<String, (u32, u32)>::new();
    let mut reasons = BTreeMap::<String, u32>::new();
    let mut elapsed = Vec::new();
    for attempt in 0..contract.verification.attempts {
        let scenario = &contract.verification.scenarios[attempt as usize % contract.verification.scenarios.len()];
        let (request_id, response) = control.request(
            Cmd::SngMega {
                bot,
                scenario: scenario_kind(&scenario.wire),
                start: scenario.start,
                mega: contract.verification.mega,
                rockets: contract.verification.rockets,
                max_secs: contract.verification.max_secs,
            },
            Duration::from_secs(10),
        );
        assert!(matches!(response, Resp::SngMega(_)));
        let result = control.wait_sng_result(
            request_id,
            Duration::from_secs_f32(contract.verification.max_secs + 12.0),
        );
        let entry = by_scenario.entry(scenario.name.clone()).or_default();
        entry.1 += 1;
        if result.ok {
            successes += 1;
            entry.0 += 1;
        }
        *reasons.entry(result.reason.clone()).or_default() += 1;
        elapsed.push(result.elapsed);
        eprintln!(
            "attempt={}/{} scenario={} ok={} reason={} elapsed={:.3}",
            attempt + 1,
            contract.verification.attempts,
            scenario.name,
            result.ok,
            result.reason,
            result.elapsed
        );
    }

    let report = serde_json::json!({
        "schema": "rtx-dm3-mega-fresh-boot-report/1",
        "patch_id": contract.id,
        "source_graph_sha256": contract.source_graph_sha256,
        "patched_graph_sha256": contract.patched_graph_sha256,
        "total_links": ready.links,
        "active_links": patch.active_links,
        "attempts": contract.verification.attempts,
        "successes": successes,
        "rate": successes as f64 / contract.verification.attempts as f64,
        "by_scenario": by_scenario,
        "reasons": reasons,
        "elapsed": elapsed,
        "walljump": 0,
        "doublejump": 0
    });
    fs::write(
        server.root.join("fresh-boot-report.json"),
        serde_json::to_vec_pretty(&report).expect("encode fresh-boot report"),
    )
    .expect("write fresh-boot report");
    eprintln!("{report}");
    assert!(
        successes >= contract.verification.minimum_successes,
        "mega acceptance failed: {successes}/{} < {}/{}",
        contract.verification.attempts,
        contract.verification.minimum_successes,
        contract.verification.attempts
    );
}
