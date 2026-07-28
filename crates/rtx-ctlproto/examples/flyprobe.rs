// Movement probe: drive the control channel directly, without the MCP bridge.
//
// Two reasons this exists next to the MCP tools rather than inside them. The bridge binary cannot
// relink on Windows while it is running, so a session that *changes* the bridge is otherwise stuck
// with the old tools until the editor restarts it — this talks the same protocol over a plain socket
// and is always current. And its output is summarised rather than dumped: a `Goto` across a map is
// ~1200 trajectory rows, which is unreadable raw but says everything once reduced to "where did it
// leave the ground" and "where did it stop making progress".
//
//   cargo run -q -p rtx-ctlproto --example flyprobe -- list
//   cargo run -q -p rtx-ctlproto --example flyprobe -- near <x> <y> <z>       # takeoffs near a point
//   cargo run -q -p rtx-ctlproto --example flyprobe -- fly   <link> [trials]  # land-rate + launch spread
//   cargo run -q -p rtx-ctlproto --example flyprobe -- trace <link> [trials]  # airborne trace of a miss
//   cargo run -q -p rtx-ctlproto --example flyprobe -- goto  <x y z> <x y z> [trials]   # route + jumps + crawls
//
// A server must be up with `rtx_control_port 27950` (what `server_start` opens).

use std::io::Write;
use std::net::TcpStream;
use std::time::{Duration, Instant};

use rtx_ctlproto::{decode, read_frame, to_frame, Cmd, CurlLink, Event, Msg, Request, Resp, Vec3};

struct Conn {
    s: TcpStream,
    id: i64,
}

impl Conn {
    fn open() -> Conn {
        let s = TcpStream::connect(("127.0.0.1", 27950)).expect("connect 27950");
        s.set_nodelay(true).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(20))).unwrap();
        Conn { s, id: 1 }
    }

    /// Send a command and pump frames until its reply arrives, returning it. Events seen on the way
    /// are handed to `on_event` (the fly result arrives as one).
    fn req(&mut self, cmd: Cmd, on_event: &mut impl FnMut(Event)) -> Result<Resp, String> {
        let id = self.id;
        self.id += 1;
        self.s
            .write_all(&to_frame(&Request { id, cmd }))
            .map_err(|e| e.to_string())?;
        loop {
            let Some(bytes) = read_frame(&mut self.s).map_err(|e| e.to_string())? else {
                return Err("connection closed".into());
            };
            match decode::<Msg>(&bytes).map_err(|e| e.to_string())? {
                Msg::Reply { id: rid, result } if rid == id => return result,
                Msg::Reply { .. } => {}
                Msg::Event(ev) => on_event(ev),
            }
        }
    }

    /// Pump frames until an event satisfies `want` or `secs` elapse.
    fn wait_event(&mut self, secs: f32, mut want: impl FnMut(&Event) -> bool) -> Option<Event> {
        let deadline = Instant::now() + Duration::from_secs_f32(secs);
        while Instant::now() < deadline {
            let Ok(Some(bytes)) = read_frame(&mut self.s) else {
                return None;
            };
            if let Ok(Msg::Event(ev)) = decode::<Msg>(&bytes) {
                if want(&ev) {
                    return Some(ev);
                }
            }
        }
        None
    }
}

fn speed_jumps(c: &mut Conn) -> Vec<CurlLink> {
    match c.req(Cmd::Curls, &mut |_| {}) {
        Ok(Resp::Curls(v)) => v,
        other => panic!("Curls -> {other:?}"),
    }
}

fn first_bot(c: &mut Conn) -> u32 {
    match c.req(Cmd::Status, &mut |_| {}) {
        Ok(Resp::Status(st)) => st.bots.first().map(|b| b.ent).expect("a live bot"),
        other => panic!("Status -> {other:?}"),
    }
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let mut c = Conn::open();
    let mode = argv.first().map(String::as_str).unwrap_or("list");

    if mode == "list" || mode == "near" {
        let links = speed_jumps(&mut c);
        let near: Option<[f32; 3]> = if mode == "near" {
            Some([
                argv[1].parse().unwrap(),
                argv[2].parse().unwrap(),
                argv[3].parse().unwrap(),
            ])
        } else {
            None
        };
        let mut n = 0;
        for l in &links {
            if let Some(p) = near {
                let d = ((l.takeoff[0] - p[0]).powi(2) + (l.takeoff[1] - p[1]).powi(2)).sqrt();
                if d > 64.0 || (l.takeoff[2] - p[2]).abs() > 32.0 {
                    continue;
                }
            }
            n += 1;
            println!(
                "link {:6} from {:?} takeoff {:?} -> {:?}  v_req {:6.1} gain {:4.1} chained {}",
                l.link, l.from, l.takeoff, l.tgt, l.v_req, l.gain, l.chained
            );
        }
        println!("({n} of {} speed-jump links)", links.len());
        return;
    }

    // goto <cellOrXYZ...> — run a Goto and summarise the trajectory instead of dumping it: the
    // airborne segments (where it jumped and where it landed) and any stretch it spends crawling,
    // which is what "it gets there and then fails navigation" looks like in the data.
    if mode == "goto" {
        let bot = first_bot(&mut c);
        let from: Vec3 = [
            argv[1].parse().unwrap(),
            argv[2].parse().unwrap(),
            argv[3].parse().unwrap(),
        ];
        let to: Vec3 = [
            argv[4].parse().unwrap(),
            argv[5].parse().unwrap(),
            argv[6].parse().unwrap(),
        ];
        let trials: u32 = argv.get(7).and_then(|s| s.parse().ok()).unwrap_or(3);
        for i in 0..trials {
            c.req(Cmd::Teleport { bot, pos: from }, &mut |_| {}).expect("teleport");
            std::thread::sleep(Duration::from_millis(250));
            c.req(Cmd::Goto { bot, pos: to }, &mut |_| {}).expect("goto");
            // The plan, read the moment the order lands: "which path does it intend" is a different
            // question from "where did it end up", and the answer is what says whether the planner or
            // the driver is at fault.
            if let Ok(Resp::Route(r)) = c.req(Cmd::Route { bot }, &mut |_| {}) {
                let mut kinds: Vec<String> = Vec::new();
                for leg in &r.legs {
                    let k = leg.kind.clone();
                    match kinds.last_mut() {
                        Some(last) if last.starts_with(&k) => {
                            let n: u32 = last.rsplit('x').next().and_then(|s| s.parse().ok()).unwrap_or(1);
                            *last = format!("{k}x{}", n + 1);
                        }
                        _ => kinds.push(format!("{k}x1")),
                    }
                }
                println!("  plan: {} legs  {}", r.legs.len(), kinds.join(" "));
                for leg in r.legs.iter().filter(|l| l.kind != "walk") {
                    println!(
                        "        {:<10} cell {:>5} -> {:>5}  {:?}",
                        leg.kind, leg.src_cell, leg.tgt_cell, leg.tgt
                    );
                }
            }
            let ev = c.wait_event(
                45.0,
                |ev| matches!(ev, Event::Arrived { bot: b, .. } | Event::GotoStall { bot: b, .. } if *b == bot),
            );
            let (label, t, dist, traj) = match ev {
                Some(Event::Arrived { t, dist, traj, .. }) => ("arrived", t, dist, traj),
                Some(Event::GotoStall { t, dist, traj, .. }) => ("STALL", t, dist, traj),
                _ => {
                    println!("trial {i}: no event");
                    continue;
                }
            };
            let secs = traj.last().map_or(0.0, |r| r[0]) - traj.first().map_or(0.0, |r| r[0]);
            println!(
                "trial {i}: {label} dist {dist:.1} t {t:.1} frames {} over {secs:.1}s",
                traj.len()
            );
            // Airborne segments: vz != 0 runs. Report takeoff and landing of each.
            let mut seg: Option<(usize, [f32; 7])> = None;
            let mut hops = 0;
            for (k, r) in traj.iter().enumerate() {
                let air = r[6] != 0.0;
                match (&seg, air) {
                    (None, true) => seg = Some((k, *r)),
                    (Some((k0, r0)), false) => {
                        let d = ((r[1] - r0[1]).powi(2) + (r[2] - r0[2]).powi(2)).sqrt();
                        if d > 100.0 {
                            hops += 1;
                            println!(
                                "    JUMP {:>4}..{:<4} ({:7.1},{:7.1},{:6.1}) |v|={:4.0} -> ({:7.1},{:7.1},{:6.1})  {:5.0}u",
                                k0,
                                k,
                                r0[1],
                                r0[2],
                                r0[3],
                                (r0[4] * r0[4] + r0[5] * r0[5]).sqrt(),
                                r[1],
                                r[2],
                                r[3],
                                d
                            );
                        }
                        seg = None;
                    }
                    _ => {}
                }
            }
            // Where it crawls: 40-frame windows whose net displacement is under 64u.
            let w = 40;
            let mut k = 0;
            while k + w < traj.len() {
                let (a, b) = (traj[k], traj[k + w]);
                let d = ((b[1] - a[1]).powi(2) + (b[2] - a[2]).powi(2)).sqrt();
                if d < 64.0 {
                    println!(
                        "    CRAWL {:>4}..{:<4} around ({:7.1},{:7.1},{:6.1})  net {:4.0}u",
                        k,
                        k + w,
                        a[1],
                        a[2],
                        a[3],
                        d
                    );
                    k += w;
                }
                k += w;
            }
            println!("    ({hops} real jumps)");
            std::thread::sleep(Duration::from_millis(200));
        }
        return;
    }

    // fly <link> [trials]
    let link: u32 = argv[1].parse().expect("link id");
    let trials: u32 = argv.get(2).and_then(|s| s.parse().ok()).unwrap_or(5);
    let links = speed_jumps(&mut c);
    let entry = links.iter().find(|l| l.link == link).expect("link not a speed jump");
    println!(
        "link {link}: from {:?} takeoff {:?} -> {:?}  v_req {:.1} gain {:.1}",
        entry.from, entry.takeoff, entry.tgt, entry.v_req, entry.gain
    );
    let bot = first_bot(&mut c);
    let from = entry.from;

    let (mut hits, mut n) = (0u32, 0u32);
    for i in 0..trials {
        c.req(Cmd::Teleport { bot, pos: from }, &mut |_| {}).expect("teleport");
        std::thread::sleep(Duration::from_millis(250));
        c.req(Cmd::Fly { bot, link }, &mut |_| {}).expect("fly");
        let ev = c.wait_event(15.0, |ev| matches!(ev, Event::FlyResult(r) if r.bot == bot));
        match ev {
            Some(Event::FlyResult(r)) => {
                n += 1;
                if r.on_target {
                    hits += 1;
                }
                // Heading of the velocity on the frame the bot leaves the ground, and the spread of
                // headings over the last stretch of run-up: the certifier only proves +-CURL_PSI_TOL
                // around one launch heading, so this is what says whether the runtime delivers it.
                let launch = r.traj.windows(2).find(|w| w[0][6] == 0.0 && w[1][6] > 0.0).map(|w| {
                    let (vx, vy) = (w[1][4], w[1][5]);
                    (vy.atan2(vx).to_degrees().rem_euclid(360.0), w[1][1], w[1][2])
                });
                let ground: Vec<f32> = r
                    .traj
                    .iter()
                    .filter(|t| t[6] == 0.0 && (t[4] * t[4] + t[5] * t[5]).sqrt() > 200.0)
                    .rev()
                    .take(12)
                    .map(|t| t[5].atan2(t[4]).to_degrees().rem_euclid(360.0))
                    .collect();
                let (lo, hi) = ground
                    .iter()
                    .fold((f32::MAX, f32::MIN), |(a, b), &x| (a.min(x), b.max(x)));
                // `trace` mode: dump the airborne frames of the first miss, so the arc that went wrong
                // can be read tick by tick against the one that worked.
                if mode == "trace" && !r.on_target && !r.timeout {
                    println!("  --- airborne trace of the miss ---");
                    let mut air = false;
                    for t in &r.traj {
                        if t[6] > 0.0 {
                            air = true;
                        }
                        if !air {
                            continue;
                        }
                        let (vx, vy) = (t[4], t[5]);
                        println!(
                            "    t={:.3} ({:7.1},{:7.1},{:6.1}) v=({:6.0},{:6.0},{:6.0}) |v|={:5.0} yaw={:6.1}",
                            t[0],
                            t[1],
                            t[2],
                            t[3],
                            vx,
                            vy,
                            t[6],
                            vx.hypot(vy),
                            vy.atan2(vx).to_degrees().rem_euclid(360.0)
                        );
                    }
                    return;
                }
                if let Some((yaw, x, y)) = launch {
                    println!(
                        "        launch yaw {yaw:6.1} at ({x:7.1},{y:7.1})   last-12 ground yaw {lo:6.1}..{hi:6.1} (spread {:.1})",
                        hi - lo
                    );
                }
                println!(
                    "  trial {i}: {:<9} miss_xy {:6.1} miss_z {:6.1} takeoff {:6.1} peak {:6.1} land {:?}",
                    if r.timeout {
                        "TIMEOUT"
                    } else if r.on_target {
                        "ok"
                    } else {
                        "MISS"
                    },
                    r.miss_xy,
                    r.miss_z,
                    r.takeoff_speed,
                    r.peak,
                    r.land
                );
            }
            _ => println!("  trial {i}: no result"),
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    println!("== {hits}/{n} on target (v_req {:.1}) ==", entry.v_req);
}
