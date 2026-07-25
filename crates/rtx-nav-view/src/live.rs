// SPDX-License-Identifier: AGPL-3.0-or-later

//! Background poller for the live overlay: connect to a running game's control channel (framed
//! msgpack of the [`rtx_ctlproto`] schema), resolve the first live bot, and stream its route to the
//! viewer's event loop ~10×/s. It reconnects forever, so it can be started before the server is up.
//! The control server is multi-client, so this attaches alongside any other client (e.g. the MCP
//! bridge) rather than displacing it.

use std::io::{self, Write};
use std::net::TcpStream;
use std::sync::mpsc::{self, Receiver, Sender};
use std::time::Duration;

use rtx_ctlproto::{self as proto, Cmd, Msg, Request, Resp};
use winit::event_loop::EventLoopProxy;

use crate::UserEvent;

/// Reconnect interval after a dropped connection or a failed connect attempt.
///
/// Short, because a dropped connection is the *normal* outcome of switching maps: the engine reloads
/// the game module on a level change, which takes the control channel down with it. Waiting long here
/// would leave the viewer showing the old map for seconds after the switch it asked for.
const RECONNECT: Duration = Duration::from_secs(1);

/// How long to wait for a reply before giving up on the connection.
///
/// Without this a lost reply wedges the poller forever: it blocks in `read_frame` on a socket the
/// game is never going to write to again, so the map never refreshes and the overlay quietly stops
/// updating, with the connection still showing as up. Replies are milliseconds away in practice, so
/// anything approaching this is a dead session — drop it and reconnect.
const REPLY_TIMEOUT: Duration = Duration::from_secs(10);

/// Spawn the poller thread. `port` is the game's `rtx_control_port` (default 27950).
///
/// The returned sender carries world points the viewer wants the bot ordered to — a click in the 3D
/// view. Sending is non-blocking and safe at any time: the poller only picks them up while a session
/// is live, so clicks made with the game down are dropped rather than replayed late.
pub fn spawn(proxy: EventLoopProxy<UserEvent>, port: u16) -> Sender<Order> {
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || run(&proxy, port, &rx));
    tx
}

/// Something the viewer wants the game to do. Queued from the UI thread and picked up by the poller
/// on its next tick, so the UI never blocks on the socket.
pub enum Order {
    /// Send the bot to a world point — a click in the 3D view.
    Goto(proto::Vec3),
    /// Change level, by name. The poller notices the new map on its next status tick and refetches
    /// the BSP, so the viewer follows the server rather than assuming the switch took.
    Map(String),
}

fn run(proxy: &EventLoopProxy<UserEvent>, port: u16, goto_rx: &Receiver<Order>) {
    let mut next_id: i64 = 1;
    loop {
        if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) {
            let _ = stream.set_nodelay(true);
            let _ = stream.set_read_timeout(Some(REPLY_TIMEOUT));
            // `send_event` errors only once the event loop (the app) has shut down — stop the poller.
            if proxy.send_event(UserEvent::LiveConnected(true)).is_err() {
                return;
            }
            // Poll until any I/O error (the game closed / was restarted), then drop out to reconnect.
            let _ = session(proxy, &mut stream, &mut next_id, goto_rx);
            if proxy.send_event(UserEvent::LiveConnected(false)).is_err() {
                return;
            }
        }
        // Retry every RECONNECT until we reconnect or the app terminates (the thread dies with the
        // process, and the send_event guards above stop it as soon as the loop is gone).
        std::thread::sleep(RECONNECT);
    }
}

/// Drain the queue, keeping only the last order of each kind — a click supersedes the previous one,
/// and anything queued while disconnected is stale by the time we reconnect.
fn drain_orders(rx: &Receiver<Order>) -> (Option<proto::Vec3>, Option<String>) {
    let (mut goto, mut map) = (None, None);
    for order in rx.try_iter() {
        match order {
            Order::Goto(p) => goto = Some(p),
            Order::Map(m) => map = Some(m),
        }
    }
    (goto, map)
}

/// How many route polls (~100ms each) between status checks. The status reply is far bigger than a
/// route, and it's only read here to notice a level change, which no one does ten times a second.
const STATUS_EVERY: u32 = 10;

/// Poll one connection: resolve the first bot from `status`, then stream its `route` until an I/O
/// error ends the session (a bad-bot reply just re-resolves — the bot may have died/respawned).
fn session(
    proxy: &EventLoopProxy<UserEvent>,
    stream: &mut TcpStream,
    next_id: &mut i64,
    order_rx: &Receiver<Order>,
) -> io::Result<()> {
    // Whatever piled up while we were down describes a game state that no longer exists.
    drain_orders(order_rx);
    // Fetch the map BSP once up front so the viewer renders the exact map the game is running —
    // no local `.bsp` needed, and it works even for maps that live only inside a `.pak` (the game
    // serves it through the engine filesystem).
    fetch_bsp(proxy, stream, next_id)?;
    // The loadable map list, once per session — it's a filesystem scan on the game side and the set
    // doesn't change while the server is up.
    match request(stream, next_id, Cmd::Maps)? {
        Ok(Resp::Maps(m)) => {
            let _ = proxy.send_event(UserEvent::Maps(m));
        }
        // An older game module doesn't know `Cmd::Maps`; the map picker just stays empty.
        other => eprintln!("navview: map list unavailable: {}", describe(&other)),
    }
    let mut bot: Option<u32> = None;
    let mut map: Option<String> = None;
    let mut tick: u32 = 0;
    loop {
        // Status doubles as bot resolution and level-change detection, so it runs whenever the bot id
        // is unknown and otherwise on a slow beat.
        if bot.is_none() || tick.is_multiple_of(STATUS_EVERY) {
            if let Ok(Resp::Status(s)) = request(stream, next_id, Cmd::Status)? {
                bot = s.bots.first().map(|b| b.ent);
                // The level changed under us (our own `map` order, a timelimit, an admin): pull the
                // new BSP so the viewer follows the server instead of drawing the old world.
                if map.as_deref() != Some(s.map.as_str()) {
                    map = Some(s.map.clone());
                    fetch_bsp(proxy, stream, next_id)?;
                }
            }
            if bot.is_none() {
                tick = tick.wrapping_add(1);
                std::thread::sleep(Duration::from_millis(300));
                continue;
            }
        }
        tick = tick.wrapping_add(1);
        let (goto, want_map) = drain_orders(order_rx);
        // A level change invalidates the bot id and the whole graph, so issue it and restart the
        // loop rather than polling a route through a world that's being torn down.
        if let Some(name) = want_map {
            // Fire and forget. A level change runs from the engine's console buffer *after* this
            // request is handled, and it takes the queued `Queued` reply down with it — so waiting
            // for one blocks until the read timeout while the very map switch we asked for goes
            // unnoticed. The next status poll is what confirms the change, and it needs no reply.
            send(
                stream,
                next_id,
                Cmd::RunCmd {
                    raw: format!("map {name}"),
                },
            )?;
            bot = None;
            map = None; // force the BSP refetch once the new level reports in
            std::thread::sleep(Duration::from_millis(500));
            continue;
        }
        // A clicked destination goes out ahead of the route poll, so the very next poll already
        // reflects the new route. `Goto` returns as soon as the order is accepted; the arrival (or
        // stall) arrives later as an event, which `request` skips past on our behalf.
        if let Some(pos) = goto {
            if request(stream, next_id, Cmd::Goto { bot: bot.unwrap(), pos })?.is_err() {
                // Stale bot id — re-resolve and poll afresh; the next click will land.
                bot = None;
                continue;
            }
        }
        match request(stream, next_id, Cmd::Route { bot: bot.unwrap() })? {
            Ok(Resp::Route(r)) => {
                let _ = proxy.send_event(UserEvent::Live(Box::new(r)));
            }
            Ok(_) => {}
            Err(_) => bot = None, // e.g. the bot id went stale — re-resolve next loop
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

/// Ask for the current map's BSP and hand it to the viewer.
///
/// A failure here leaves the window empty, which used to happen silently — the reply was pattern
/// matched with `if let Ok(Resp::Bsp(..))` and anything else fell on the floor, so a game that
/// couldn't read its own map looked identical to a viewer that never asked. Say what went wrong.
fn fetch_bsp(proxy: &EventLoopProxy<UserEvent>, stream: &mut TcpStream, next_id: &mut i64) -> io::Result<()> {
    match request(stream, next_id, Cmd::Bsp)? {
        Ok(Resp::Bsp(b)) => {
            let _ = proxy.send_event(UserEvent::Bsp(b));
        }
        other => eprintln!("navview: could not fetch the map BSP: {}", describe(&other)),
    }
    Ok(())
}

/// A one-line description of a reply that wasn't what we asked for — a game-side error message, or
/// the variant we got instead (which means the two ends disagree about the protocol).
fn describe(r: &Result<Resp, String>) -> String {
    match r {
        Ok(other) => format!("unexpected reply {other:?}"),
        Err(e) => e.clone(),
    }
}

/// Write one request without waiting for its reply — for commands whose reply may never arrive (see
/// the level change in [`session`]). A late reply is harmless: [`request`] skips any frame whose id
/// isn't the one it's waiting on.
fn send(stream: &mut TcpStream, next_id: &mut i64, cmd: Cmd) -> io::Result<()> {
    let id = *next_id;
    *next_id += 1;
    stream.write_all(&proto::to_frame(&Request { id, cmd }))?;
    stream.flush()
}

/// Send one request and return its typed reply, skipping any async events that arrive first. Returns
/// `Err` only on I/O trouble; a game-side error surfaces as `Ok(Err(msg))`.
fn request(stream: &mut TcpStream, next_id: &mut i64, cmd: Cmd) -> io::Result<Result<Resp, String>> {
    let id = *next_id;
    *next_id += 1;
    stream.write_all(&proto::to_frame(&Request { id, cmd }))?;
    stream.flush()?;
    loop {
        let Some(frame) = proto::read_frame(stream)? else {
            return Err(io::Error::from(io::ErrorKind::UnexpectedEof));
        };
        match proto::decode::<Msg>(&frame).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))? {
            // Our reply — or a bad-frame error the game couldn't attribute to a request, which it
            // sends with id 0 (e.g. an older module that doesn't know `Cmd::Bsp`). Since we keep only
            // one request in flight, that unattributed error is ours: surface it rather than spin.
            Msg::Reply { id: rid, result } if rid == id || rid == 0 => return Ok(result),
            _ => continue, // an event, or a reply we're not waiting on — keep reading
        }
    }
}
