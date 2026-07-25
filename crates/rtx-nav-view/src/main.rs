// SPDX-License-Identifier: AGPL-3.0-or-later

//! `navview` — a minimal wgpu viewer for the `rtx` bot navmesh. Renders a Quake BSP's world model as
//! untextured grey geometry and overlays the navmesh with one color per [`LinkKind`], the ballistic
//! link kinds drawn as their true arcs. Load a map by passing it as `argv[1]` or by dropping a `.bsp`
//! onto the window. A noclip-style fly camera moves with WASD + Space/C and looks with the right
//! mouse button held.
//!
//! The mesh is built with the game's **stock loadout**, so the overlay is the one bots navigate; the
//! arcade movement options the game ships disabled (double jump, grapple) are opt-in and rebuild the
//! navmesh when ticked — see [`BUILD_GATED`].

mod geom;
mod gpu;
mod live;

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

use glam::{Mat4, Vec3};
use rtx_nav::bsp::Bsp;
use rtx_nav::navmesh::{
    build_navmesh, HookParams, LinkKind, NavGraph, RocketJumpParams, SpeedJumpParams, HOOK_PULL_BASE, HOOK_THROW_BASE,
};

use geom::NUM_LINK_KINDS;
use winit::application::ApplicationHandler;
use winit::event::{DeviceEvent, DeviceId, ElementState, MouseButton, WindowEvent};
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop, EventLoopProxy};
use winit::keyboard::{KeyCode, PhysicalKey};
use winit::window::{CursorGrabMode, Window, WindowId};

use gpu::Gpu;

/// Delivered from the background navmesh-build thread back to the event loop. The BSP is parsed on
/// the main thread and shared into the worker, so it rides back alongside the finished graph.
enum UserEvent {
    NavBuilt {
        generation: u64,
        bsp: Arc<Bsp>,
        graph: NavGraph,
    },
    /// A live route poll from a running game: the bot's origin plus its current route legs.
    Live(Box<rtx_ctlproto::RouteResp>),
    /// The map BSP fetched over the control channel — so `--live` needn't be given a local `.bsp`
    /// (and works for maps that live only inside a `.pak`, which the game reads via the engine FS).
    Bsp(Box<rtx_ctlproto::BspResp>),
    /// The live control-channel connection came up (`true`) or dropped (`false`).
    LiveConnected(bool),
    /// Every map the connected server could load, for the map picker.
    Maps(Vec<String>),
}

/// A noclip fly camera: a position plus yaw/pitch look angles (Quake Z-up, right-handed).
struct FlyCamera {
    pos: Vec3,
    yaw: f32,
    pitch: f32,
}

impl FlyCamera {
    fn dir(&self) -> Vec3 {
        let (cp, sp) = (self.pitch.cos(), self.pitch.sin());
        Vec3::new(cp * self.yaw.cos(), cp * self.yaw.sin(), sp)
    }

    fn view_proj(&self, aspect: f32) -> Mat4 {
        let proj = Mat4::perspective_rh(60f32.to_radians(), aspect.max(0.01), 4.0, 32768.0);
        proj * Mat4::look_to_rh(self.pos, self.dir(), Vec3::Z)
    }

    /// Unproject a cursor position (physical pixels) into a world ray `(origin, unit direction)`.
    /// `None` for a degenerate viewport or a camera basis that can't be inverted.
    ///
    /// The projection is `Mat4::perspective_rh`, so clip depth runs 0 (near) to 1 (far) — wgpu's
    /// convention, not OpenGL's −1..1 — and the two unprojected points bracket the ray.
    fn pick_ray(&self, aspect: f32, size: (f32, f32), cursor: (f32, f32)) -> Option<(Vec3, Vec3)> {
        let (w, h) = size;
        if w < 1.0 || h < 1.0 {
            return None;
        }
        let inv = self.view_proj(aspect).inverse();
        if !inv.is_finite() {
            return None;
        }
        let ndc_x = 2.0 * cursor.0 / w - 1.0;
        let ndc_y = 1.0 - 2.0 * cursor.1 / h; // window Y grows downward, NDC Y upward
        let near = inv.project_point3(Vec3::new(ndc_x, ndc_y, 0.0));
        let far = inv.project_point3(Vec3::new(ndc_x, ndc_y, 1.0));
        let dir = (far - near).try_normalize()?;
        Some((near, dir))
    }

    /// Frame the whole map: stand back from a high corner and look at the center.
    fn frame(&mut self, mins: Vec3, maxs: Vec3) {
        let center = (mins + maxs) * 0.5;
        let extent = (maxs - mins).length().max(64.0);
        self.pos = center + Vec3::new(0.9, 0.9, 0.7).normalize() * (extent * 0.6);
        let look = (center - self.pos).normalize_or(Vec3::NEG_X);
        self.yaw = look.y.atan2(look.x);
        self.pitch = look.z.clamp(-0.999, 0.999).asin();
    }
}

impl Default for FlyCamera {
    fn default() -> Self {
        FlyCamera {
            pos: Vec3::new(-256.0, 0.0, 128.0),
            yaw: 0.0,
            pitch: -0.3,
        }
    }
}

struct App {
    window: Option<Arc<Window>>,
    gpu: Option<Gpu>,
    camera: FlyCamera,
    keys: HashSet<KeyCode>,
    looking: bool,
    fast: bool,
    last_tick: Instant,
    proxy: EventLoopProxy<UserEvent>,
    generation: u64,
    pending_path: Option<PathBuf>,
    /// The most recently built navmesh, kept with its BSP so the overlay can be regenerated when a
    /// path-type toggle changes without rebuilding the graph (the BSP is needed to trim each cell's
    /// filled tile to its hull-1-supported footprint in [`geom::nav_clusters`]). Also what
    /// [`Self::rebuild_navmesh`] re-solves from when a build-gated path type is toggled.
    nav: Option<(Arc<Bsp>, NavGraph)>,
    /// Per-`LinkKind` visibility (indexed by `geom::kind_index`); `Walk` gates the filled surface.
    /// For the [`BUILD_GATED`] kinds this doubles as the build switch — see [`BuildOpts`].
    visible: [bool; NUM_LINK_KINDS],
    /// Optional movement solvers the live navmesh was built with; toggling one rebuilds it.
    build_opts: BuildOpts,
    /// Mapname (no extension) of the currently loaded BSP, so a repeated control-channel BSP fetch
    /// (e.g. after a reconnect) skips rebuilding the navmesh for a map we already have.
    loaded_map: Option<String>,
    /// A BSP fetched over the control channel before the window/GPU existed; loaded once `resumed`
    /// brings the renderer up.
    pending_bsp: Option<Box<rtx_ctlproto::BspResp>>,
    /// Whether the `--live` poller was started (so the panel shows a connection status).
    live_mode: bool,
    /// Whether the live control-channel poller is currently connected to a running game.
    live_connected: bool,
    /// Order channel into the live poller — a click's destination, or a map change. `None` unless
    /// started with `--live`.
    orders: Option<std::sync::mpsc::Sender<live::Order>>,
    /// Maps the connected server can load, for the picker. Empty until the list arrives.
    maps: Vec<String>,
    /// Last known cursor position in physical pixels — the pick ray's screen origin.
    cursor: (f32, f32),
    /// The nav cell under the cursor: highlighted in the 3D view and read out in the corner.
    hovered: Option<u32>,
    egui_ctx: egui::Context,
    /// egui's winit input translator; created with the window in `resumed`.
    egui_state: Option<egui_winit::State>,
}

/// Path types the viewer only generates **on request**: the arcade movement options the game ships
/// disabled (`rtx_doublejump 0`, no grapple). Their checkboxes drive the *build*, not just line
/// visibility — a solver that never ran leaves no lines to unhide — so ticking one re-runs the
/// navmesh with that solver on.
///
/// They default off so the overlay matches the mesh bots actually navigate. Building them by default
/// was actively misleading: with double jump on, aerowalk grew a rocket jump onto the red-armour
/// shelf that the stock build has no way to reach, so the viewer showed a route the bots didn't have.
const BUILD_GATED: [LinkKind; 2] = [LinkKind::DoubleJump, LinkKind::Hook];

/// Which optional movement solvers the current navmesh was built with (see [`BUILD_GATED`]).
#[derive(Clone, Copy, Default, PartialEq, Eq)]
struct BuildOpts {
    double_jump: bool,
    hook: bool,
}

impl BuildOpts {
    /// The build flag a path-type checkbox drives, or `None` for a kind that's pure display.
    fn slot(&mut self, kind: LinkKind) -> Option<&mut bool> {
        match kind {
            LinkKind::DoubleJump => Some(&mut self.double_jump),
            LinkKind::Hook => Some(&mut self.hook),
            _ => None,
        }
    }
}

/// How far a picked surface point may sit from a cell's origin and still count as hovering it.
/// `NavGraph::nearest` searches several grid columns out, so without a cutoff, pointing at a bare
/// wall would light up whatever cell happens to be nearest. A legitimate floor hit is at most half a
/// tile out horizontally and a feet-drop down (`√(16² + 16² + 24²) ≈ 33`), so this leaves room for
/// steps and slopes without reaching across a room.
const HOVER_MAX_DIST: f32 = 64.0;

/// Default window size. **Physical** pixels, not logical: 1080p worth of actual framebuffer whatever
/// the display's scale factor is, rather than a window that balloons past the screen at 150% DPI.
const DEFAULT_WINDOW: winit::dpi::PhysicalSize<u32> = winit::dpi::PhysicalSize::new(1920, 1080);

/// Base fly speed (units/sec); Shift multiplies it.
const MOVE_SPEED: f32 = 320.0;
const FAST_MULT: f32 = 4.0;
const LOOK_SENS: f32 = 0.003;
const PITCH_LIMIT: f32 = 1.55; // just under 90°

impl App {
    fn new(proxy: EventLoopProxy<UserEvent>, pending_path: Option<PathBuf>) -> Self {
        // Everything visible except the build-gated kinds, which start unticked to match a stock build.
        let mut visible = [true; NUM_LINK_KINDS];
        for kind in BUILD_GATED {
            visible[geom::kind_index(kind)] = false;
        }
        App {
            window: None,
            gpu: None,
            camera: FlyCamera::default(),
            keys: HashSet::new(),
            looking: false,
            fast: false,
            last_tick: Instant::now(),
            proxy,
            generation: 0,
            pending_path,
            nav: None,
            visible,
            build_opts: BuildOpts::default(),
            loaded_map: None,
            pending_bsp: None,
            live_mode: false,
            live_connected: false,
            orders: None,
            maps: Vec::new(),
            cursor: (0.0, 0.0),
            hovered: None,
            egui_ctx: egui::Context::default(),
            egui_state: None,
        }
    }

    /// The nav cell under the cursor, if any — the basis of both the hover highlight and the
    /// `--live` click-to-goto order.
    ///
    /// The cursor is unprojected into a world ray and traced against the map's **point** hull, so the
    /// hit is the surface actually drawn under the cursor rather than wherever a player bounding box
    /// would jam. The hit is lifted off its plane (a floor click should resolve to the cell standing
    /// on it, not to whatever sits below a thin brush) and snapped to the nearest cell.
    ///
    /// `nearest` searches a few grid columns out and will happily answer for a click on a bare wall,
    /// so the result is rejected past [`HOVER_MAX_DIST`] — pointing at nothing highlights nothing.
    fn pick_cell(&self) -> Option<u32> {
        if self.looking {
            return None; // the pointer is grabbed for looking; its position means nothing
        }
        let (gpu, (bsp, graph)) = (self.gpu.as_ref()?, self.nav.as_ref()?);
        let (from, dir) = self.camera.pick_ray(gpu.aspect(), gpu.size(), self.cursor)?;
        // The far plane's distance: anything the camera can see is within it.
        let hit = bsp.hull0_trace(from, from + dir * 32768.0);
        if hit.fraction >= 1.0 || hit.start_solid {
            return None; // pointing at the void, or the camera is buried in geometry
        }
        let at = hit.endpos + hit.plane_normal;
        let cell = graph.nearest(at)?;
        (graph.cell_origin(cell).distance(at) <= HOVER_MAX_DIST).then_some(cell)
    }

    /// Re-pick the hovered cell and, when it changed, swap the highlight tiles on the GPU. Runs every
    /// frame rather than only on cursor motion, so flying the camera past a cell updates it too.
    fn update_hover(&mut self) {
        let want = self.pick_cell();
        if want == self.hovered {
            return;
        }
        self.hovered = want;
        let verts = match (want, self.nav.as_ref()) {
            (Some(cell), Some((bsp, graph))) => geom::cell_highlight(graph, bsp, cell),
            _ => Vec::new(),
        };
        if let Some(gpu) = self.gpu.as_mut() {
            gpu.set_hover(&verts);
        }
    }

    /// Order the live bot to the cell under the cursor — the `--live` left-click command. Sends the
    /// cell's *origin*, so the game gets a standing position rather than a point on the floor 24u
    /// below it that it would have to re-snap blind. A click not over a cell does nothing.
    fn goto_under_cursor(&mut self) {
        let (Some(tx), Some(cell), Some((_, graph))) = (self.orders.as_ref(), self.pick_cell(), self.nav.as_ref())
        else {
            return;
        };
        let _ = tx.send(live::Order::Goto(graph.cell_origin(cell).to_array()));
    }

    /// Regenerate and upload the navmesh overlay (filled walkable surface + colored link lines) from
    /// the current graph and path-type visibility. Cheap enough to redo on every toggle change.
    ///
    /// The walkable surface is always tinted by LOD cluster and always wears the per-cell wireframe —
    /// both used to be toggles, but they're the readable default and nothing wanted them off.
    fn rebuild_overlay(&mut self) {
        let (Some(gpu), Some((bsp, graph))) = (self.gpu.as_mut(), self.nav.as_ref()) else {
            return;
        };
        if self.visible[geom::kind_index(LinkKind::Walk)] {
            gpu.set_surface(&geom::nav_clusters(graph, bsp));
            gpu.set_cellwire(&geom::nav_cell_wire(graph));
        } else {
            gpu.set_surface(&[]);
            gpu.set_cellwire(&[]);
        }
        gpu.set_lines(&geom::nav_lines(graph, &self.visible));
        if let Some(w) = &self.window {
            w.request_redraw();
        }
    }

    /// Rebuild the live overlay (route tiles + rocket/speed-jump arcs + bot cube) from one route poll.
    /// The cells and arcs come straight from the game's leg world coordinates, so they align with the
    /// map regardless of whether navview's own navmesh build matches the game's exactly.
    fn apply_live(&mut self, route: &rtx_ctlproto::RouteResp) {
        let Some(gpu) = self.gpu.as_mut() else { return };
        // Path cells: each leg's source cell, plus the final leg's target.
        let mut origins: Vec<Vec3> = route.legs.iter().map(|l| Vec3::from_array(l.src)).collect();
        if let Some(last) = route.legs.last() {
            origins.push(Vec3::from_array(last.tgt));
        }
        // Only the ballistic legs get the thick red arc.
        let arcs: Vec<(Vec3, Vec3)> = route
            .legs
            .iter()
            .filter(|l| l.kind == "rocketjump" || l.kind == "speedjump")
            .map(|l| (Vec3::from_array(l.src), Vec3::from_array(l.tgt)))
            .collect();
        let origin = Vec3::from_array(route.origin);
        gpu.set_path(&geom::path_tiles(&origins));
        gpu.set_arcs(&geom::path_arcs(&arcs));
        gpu.set_bot_faces(&geom::bot_faces(origin));
        gpu.set_bot(&geom::bot_box(origin));
    }

    /// Run one egui frame and render the scene + UI. egui is cheap; a toggle change regenerates the
    /// overlay buffers before the draw so the change shows this frame.
    fn draw(&mut self) {
        let Some(window) = self.window.clone() else { return };
        if self.egui_state.is_none() || self.gpu.is_none() {
            return;
        }

        // Re-pick before the UI runs, so the highlight and the corner readout agree this frame.
        self.update_hover();
        let hover = self
            .hovered
            .and_then(|c| self.nav.as_ref().map(|(_, g)| (c, g.cell_origin(c))));

        let raw_input = self.egui_state.as_mut().unwrap().take_egui_input(&window);
        let ctx = self.egui_ctx.clone();
        let mut visible = self.visible;
        let live = self.live_mode.then_some(self.live_connected);
        let (maps, current_map) = (std::mem::take(&mut self.maps), self.loaded_map.clone());
        let mut pick_map = None;
        let full = ctx.run_ui(raw_input, |ui| {
            build_panel(ui, &mut visible, live, &maps, current_map.as_deref(), &mut pick_map);
            hover_readout(ui, hover);
        });
        self.maps = maps;
        if let (Some(name), Some(tx)) = (pick_map, self.orders.as_ref()) {
            let _ = tx.send(live::Order::Map(name.clone()));
            self.set_title(&format!("navview — switching to {name}…"));
        }
        self.egui_state
            .as_mut()
            .unwrap()
            .handle_platform_output(&window, full.platform_output);

        if visible != self.visible {
            self.visible = visible;
            // A build-gated kind changed: its solver has to run (or stop running) before there are
            // any lines to show, so re-solve the navmesh instead of just redrawing the overlay.
            let mut opts = self.build_opts;
            for kind in BUILD_GATED {
                if let Some(slot) = opts.slot(kind) {
                    *slot = visible[geom::kind_index(kind)];
                }
            }
            if opts != self.build_opts {
                self.build_opts = opts;
                self.rebuild_navmesh();
            }
            self.rebuild_overlay();
        }

        let ppp = full.pixels_per_point;
        let jobs = ctx.tessellate(full.shapes, ppp);
        let gpu = self.gpu.as_mut().unwrap();
        gpu.render(self.camera.view_proj(gpu.aspect()), &full.textures_delta, &jobs, ppp);
    }

    fn set_title(&self, text: &str) {
        if let Some(w) = &self.window {
            w.set_title(text);
        }
    }

    /// Load a BSP from disk: show its grey geometry immediately, then build the navmesh off-thread.
    fn load(&mut self, path: &Path) {
        let name = path
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        let bytes = match std::fs::read(path) {
            Ok(b) => b,
            Err(e) => {
                self.set_title(&format!("navview — {name}: read error: {e}"));
                return;
            }
        };
        self.load_bytes(&bytes, &name);
    }

    /// Load a BSP already in memory (from disk, or fetched over the control channel). Uploads the
    /// grey geometry immediately and builds the navmesh on a worker thread. `name` may carry a
    /// `.bsp` suffix or not; the stem is remembered in [`Self::loaded_map`] to dedupe refetches.
    fn load_bytes(&mut self, bytes: &[u8], name: &str) {
        let Some(gpu) = self.gpu.as_mut() else { return };
        let Some(mesh) = geom::parse_render_mesh(bytes) else {
            eprintln!("navview: {name}: render lumps unreadable ({} bytes)", bytes.len());
            self.set_title(&format!("navview — {name}: not a supported BSP"));
            return;
        };

        gpu.set_mesh(&mesh.vertices);
        gpu.set_water(&mesh.water);
        gpu.clear_overlay();
        self.nav = None;
        self.camera.frame(mesh.mins, mesh.maxs);
        self.set_title(&format!("navview — {name} (building navmesh…)"));
        if let Some(w) = &self.window {
            w.request_redraw();
        }
        self.loaded_map = Some(name.strip_suffix(".bsp").unwrap_or(name).to_string());

        // Parse the BSP once on the main thread; the worker shares it (`Arc`) to build, and it rides
        // back with the graph for the overlay's liquid/hull queries.
        let Some(bsp) = Bsp::parse(bytes).map(Arc::new) else {
            eprintln!("navview: {name}: BSP parse failed ({} bytes)", bytes.len());
            self.set_title(&format!("navview — {name}: BSP parse failed"));
            return;
        };
        self.spawn_build(bsp);
    }

    /// Re-solve the current map's navmesh under the current [`BuildOpts`] — what a build-gated
    /// path-type checkbox triggers. No-op before the first map loads.
    fn rebuild_navmesh(&mut self) {
        let Some((bsp, _)) = self.nav.as_ref() else { return };
        let bsp = bsp.clone();
        let name = self.loaded_map.clone().unwrap_or_default();
        self.set_title(&format!("navview — {name} (rebuilding navmesh…)"));
        self.spawn_build(bsp);
    }

    /// Kick off a background navmesh build for `bsp` at the current [`BuildOpts`], superseding any
    /// build already in flight (the generation counter makes a stale worker's result get dropped).
    ///
    /// Stock-DM loadout: speed jumps (bhop, curl on) and rocket jumps at stock physics, always on
    /// because they need no cvar. Double jump and the grapple are opt-in via [`BUILD_GATED`], matching
    /// the game's shipped defaults. Plats/teleports/gates need live entities we don't have offline
    /// (empty vecs), so those links are simply absent here.
    fn spawn_build(&mut self, bsp: Arc<Bsp>) {
        self.generation += 1;
        let generation = self.generation;
        let proxy = self.proxy.clone();
        let opts = self.build_opts;
        std::thread::spawn(move || {
            let graph = build_navmesh(
                &bsp,
                Vec::new(),
                Vec::new(),
                Vec::new(),
                opts.hook.then_some(HookParams {
                    gravity: 800.0,
                    pull: HOOK_PULL_BASE,
                    throw: HOOK_THROW_BASE,
                }),
                opts.double_jump,
                Some(SpeedJumpParams {
                    gravity: 800.0,
                    accel: 10.0,
                    maxspeed: 320.0,
                    friction: 4.0,
                    stopspeed: 100.0,
                    curl: true,
                }),
                Some(RocketJumpParams {
                    gravity: 800.0,
                    rj_extra: 0.0,
                }),
            );
            let _ = proxy.send_event(UserEvent::NavBuilt { generation, bsp, graph });
        });
    }

    /// Advance the fly camera by the movement keys currently held. Returns whether it moved.
    fn integrate(&mut self, dt: f32) -> bool {
        let mut delta = Vec3::ZERO;
        let dir = self.camera.dir();
        let right = dir.cross(Vec3::Z).normalize_or_zero();
        let mut add = |cond: bool, v: Vec3| {
            if cond {
                delta += v;
            }
        };
        add(self.keys.contains(&KeyCode::KeyW), dir);
        add(self.keys.contains(&KeyCode::KeyS), -dir);
        add(self.keys.contains(&KeyCode::KeyD), right);
        add(self.keys.contains(&KeyCode::KeyA), -right);
        add(self.keys.contains(&KeyCode::Space), Vec3::Z);
        add(self.keys.contains(&KeyCode::KeyC), -Vec3::Z);
        if delta == Vec3::ZERO {
            return false;
        }
        let speed = MOVE_SPEED * if self.fast { FAST_MULT } else { 1.0 };
        self.camera.pos += delta.normalize_or_zero() * speed * dt;
        true
    }

    fn set_looking(&mut self, on: bool) {
        self.looking = on;
        let Some(w) = &self.window else { return };
        w.set_cursor_visible(!on);
        if on {
            // Locked is ideal but unsupported on some platforms — fall back to Confined.
            let _ = w
                .set_cursor_grab(CursorGrabMode::Locked)
                .or_else(|_| w.set_cursor_grab(CursorGrabMode::Confined));
        } else {
            let _ = w.set_cursor_grab(CursorGrabMode::None);
        }
    }
}

impl ApplicationHandler<UserEvent> for App {
    fn resumed(&mut self, el: &ActiveEventLoop) {
        if self.window.is_some() {
            return;
        }
        let attrs = Window::default_attributes()
            .with_title("navview — drop a .bsp")
            .with_inner_size(DEFAULT_WINDOW);
        let window = Arc::new(el.create_window(attrs).expect("create window"));
        self.gpu = Some(Gpu::new(window.clone()));
        self.egui_state = Some(egui_winit::State::new(
            self.egui_ctx.clone(),
            egui::ViewportId::ROOT,
            window.as_ref(),
            Some(window.scale_factor() as f32),
            None,
            None,
        ));
        window.request_redraw();
        self.window = Some(window);
        if let Some(path) = self.pending_path.take() {
            self.load(&path);
        }
        // A BSP that arrived over the control channel before the GPU existed: load it now.
        if let Some(bsp) = self.pending_bsp.take() {
            self.load_bytes(&bsp.bytes, &bsp.map);
        }
    }

    fn window_event(&mut self, el: &ActiveEventLoop, _id: WindowId, event: WindowEvent) {
        // Let egui see the event first; if it consumed it (a click on the panel, typing in it),
        // don't also treat it as camera / hotkey input.
        let window = self.window.clone();
        if let (Some(window), Some(state)) = (window, self.egui_state.as_mut()) {
            let resp = state.on_window_event(&window, &event);
            if resp.repaint {
                window.request_redraw();
            }
            if resp.consumed {
                return;
            }
        }

        match event {
            WindowEvent::CloseRequested => el.exit(),
            WindowEvent::Resized(size) => {
                if let Some(gpu) = &mut self.gpu {
                    gpu.resize(size.width, size.height);
                }
            }
            WindowEvent::DroppedFile(path) => self.load(&path),
            WindowEvent::RedrawRequested => self.draw(),
            WindowEvent::CursorMoved { position, .. } => {
                self.cursor = (position.x as f32, position.y as f32);
                // The hover pick happens in `draw`; ask for the frame that will run it.
                if let Some(w) = &self.window {
                    w.request_redraw();
                }
            }
            WindowEvent::MouseInput {
                state,
                button: MouseButton::Right,
                ..
            } => {
                self.set_looking(state == ElementState::Pressed);
            }
            // Left click in the 3D view orders the live bot to the spot under the cursor. Clicks on
            // the egui panel never reach here (consumed above), and while the right button holds the
            // cursor for looking there's no meaningful screen position to pick from.
            WindowEvent::MouseInput {
                state: ElementState::Pressed,
                button: MouseButton::Left,
                ..
            } if !self.looking => self.goto_under_cursor(),
            WindowEvent::KeyboardInput { event, .. } => {
                if let PhysicalKey::Code(code) = event.physical_key {
                    let pressed = event.state == ElementState::Pressed;
                    if code == KeyCode::ShiftLeft || code == KeyCode::ShiftRight {
                        self.fast = pressed;
                    } else if code == KeyCode::Escape && pressed {
                        el.exit();
                    } else if pressed {
                        self.keys.insert(code);
                    } else {
                        self.keys.remove(&code);
                    }
                }
            }
            _ => {}
        }
    }

    fn device_event(&mut self, _el: &ActiveEventLoop, _id: DeviceId, event: DeviceEvent) {
        if self.looking {
            if let DeviceEvent::MouseMotion { delta: (dx, dy) } = event {
                self.camera.yaw -= dx as f32 * LOOK_SENS;
                self.camera.pitch = (self.camera.pitch - dy as f32 * LOOK_SENS).clamp(-PITCH_LIMIT, PITCH_LIMIT);
                if let Some(w) = &self.window {
                    w.request_redraw();
                }
            }
        }
    }

    fn user_event(&mut self, _el: &ActiveEventLoop, event: UserEvent) {
        match event {
            UserEvent::NavBuilt { generation, bsp, graph } => {
                if generation != self.generation {
                    return; // a newer map was dropped while this build ran — discard the stale result
                }
                self.set_title(&format!(
                    "navview — {} cells, {} links",
                    graph.cells.len(),
                    graph.links.len()
                ));
                eprintln!("TRACE NavBuilt cells={} links={}", graph.cells.len(), graph.links.len());
                self.nav = Some((bsp, graph));
                self.rebuild_overlay();
            }
            UserEvent::Live(route) => self.apply_live(&route),
            UserEvent::Bsp(bsp) => {
                eprintln!(
                    "TRACE Bsp event map={:?} bytes={} loaded_map={:?} gpu={}",
                    bsp.map,
                    bsp.bytes.len(),
                    self.loaded_map,
                    self.gpu.is_some()
                );
                // Skip if we already have this map (e.g. a refetch after a reconnect); otherwise load
                // now, or stash it for `resumed` if the renderer isn't up yet.
                if self.loaded_map.as_deref() == Some(bsp.map.as_str()) {
                } else if self.gpu.is_some() {
                    self.load_bytes(&bsp.bytes, &bsp.map);
                } else {
                    self.pending_bsp = Some(bsp);
                }
            }
            UserEvent::LiveConnected(up) => {
                self.live_connected = up;
                if !up {
                    if let Some(gpu) = self.gpu.as_mut() {
                        gpu.clear_live();
                    }
                }
            }
            UserEvent::Maps(maps) => self.maps = maps,
        }
        if let Some(w) = &self.window {
            w.request_redraw();
        }
    }

    fn about_to_wait(&mut self, el: &ActiveEventLoop) {
        let now = Instant::now();
        let dt = (now - self.last_tick).as_secs_f32().min(0.05); // clamp to avoid post-idle jumps
        self.last_tick = now;
        let moving = self.integrate(dt);
        if moving {
            if let Some(w) = &self.window {
                w.request_redraw();
            }
        }
        // Poll (drive continuous movement) only while a move key is held; otherwise idle in Wait.
        el.set_control_flow(if self.keys.is_empty() {
            ControlFlow::Wait
        } else {
            ControlFlow::Poll
        });
    }
}

/// Axis tints for the origin readout — the near-universal X=red, Y=green, Z=blue convention, each
/// lightened enough to stay legible on the popup's dark backing (a full-strength blue does not).
const AXIS_COLORS: [egui::Color32; 3] = [
    egui::Color32::from_rgb(255, 96, 96),
    egui::Color32::from_rgb(120, 224, 120),
    egui::Color32::from_rgb(120, 168, 255),
];

/// The hovered-cell readout, pinned to the bottom-left corner: the cell id in white, then the world
/// origin with each component tinted by its axis. Nothing is drawn when nothing is hovered, so the
/// corner stays clean while flying around.
fn hover_readout(ui: &mut egui::Ui, hover: Option<(u32, Vec3)>) {
    let Some((cell, o)) = hover else { return };
    egui::Area::new(egui::Id::new("hover-readout"))
        .anchor(egui::Align2::LEFT_BOTTOM, [12.0, -12.0])
        .interactable(false)
        .show(ui.ctx(), |ui| {
            egui::Frame::popup(ui.style()).show(ui, |ui| {
                // An anchored area offers no width to lay out against, so the default wrap mode would
                // break the readout at every space — one word per line. Size to the content instead.
                ui.style_mut().wrap_mode = Some(egui::TextWrapMode::Extend);
                ui.horizontal(|ui| {
                    ui.colored_label(egui::Color32::WHITE, format!("cell {cell}"));
                    // Origins land on the 32u grid in XY but carry a fractional Z from the floor trace.
                    for (v, col) in [o.x, o.y, o.z].into_iter().zip(AXIS_COLORS) {
                        ui.colored_label(col, format!("{v:.1}"));
                    }
                });
            });
        });
}

/// The path-type toggle panel: a checkbox per `LinkKind`, labelled and swatched in that kind's
/// overlay color. `Walk` toggles the filled walkable surface; the rest toggle their colored lines.
///
/// The [`BUILD_GATED`] kinds are marked with a `*` and a tooltip: their solver is off by default (the
/// game's stock loadout), so ticking one re-runs the navmesh build rather than just unhiding lines.
fn build_panel(
    ui: &mut egui::Ui,
    visible: &mut [bool; NUM_LINK_KINDS],
    live: Option<bool>,
    maps: &[String],
    current_map: Option<&str>,
    pick_map: &mut Option<String>,
) {
    egui::Window::new("Path types")
        .default_pos([12.0, 12.0])
        .resizable(false)
        .show(ui.ctx(), |ui| {
            for kind in geom::LINK_KINDS {
                let [r, g, b] = geom::link_color(kind);
                let swatch = egui::Color32::from_rgb((r * 255.0) as u8, (g * 255.0) as u8, (b * 255.0) as u8);
                let gated = BUILD_GATED.contains(&kind);
                ui.horizontal(|ui| {
                    ui.checkbox(&mut visible[geom::kind_index(kind)], "");
                    let label = if gated {
                        format!("{} *", geom::kind_label(kind))
                    } else {
                        geom::kind_label(kind).to_string()
                    };
                    let resp = ui.colored_label(swatch, label);
                    if gated {
                        resp.on_hover_text("Off in the game's stock loadout — toggling rebuilds the navmesh");
                    }
                });
            }
            // Live overlay status (only when started with `--live`): the current route is drawn as
            // red cells, ballistic legs as thick red arcs, and the bot as a yellow bounding box.
            if let Some(connected) = live {
                ui.separator();
                let (col, txt) = if connected {
                    (egui::Color32::from_rgb(255, 60, 40), "live: connected")
                } else {
                    (egui::Color32::GRAY, "live: waiting for game…")
                };
                ui.colored_label(col, txt);
                if connected {
                    ui.weak("left-click the map to send the bot there");
                }
                // The map picker: the server's own list of loadable maps, current one selected.
                // Choosing another changes level, which the poller then follows.
                if connected && !maps.is_empty() {
                    let shown = current_map.unwrap_or("—");
                    egui::ComboBox::from_id_salt("map-picker")
                        .selected_text(shown)
                        .show_ui(ui, |ui| {
                            for name in maps {
                                // `selectable_label` rather than `selectable_value`: the selection is
                                // owned by the server, so a click is a *request*, not a local change.
                                if ui.selectable_label(current_map == Some(name.as_str()), name).clicked()
                                    && current_map != Some(name.as_str())
                                {
                                    *pick_map = Some(name.clone());
                                }
                            }
                        });
                }
            }
        });
}

fn main() {
    let event_loop = EventLoop::<UserEvent>::with_user_event().build().expect("event loop");
    event_loop.set_control_flow(ControlFlow::Wait);
    let proxy = event_loop.create_proxy();

    // Args: an optional positional `.bsp` path, and `--live [port]` (or `--connect [port]`) to attach
    // the live overlay to a running game's control channel (default `rtx_control_port` = 27950).
    let mut pending_path = None;
    let mut live_port: Option<u16> = None;
    let mut args = std::env::args().skip(1).peekable();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--live" | "--connect" => {
                let port = args.peek().and_then(|s| s.parse::<u16>().ok());
                if port.is_some() {
                    args.next();
                }
                live_port = Some(port.unwrap_or(27950));
            }
            _ if pending_path.is_none() => pending_path = Some(PathBuf::from(arg)),
            _ => {}
        }
    }

    let mut app = App::new(proxy.clone(), pending_path);
    if let Some(port) = live_port {
        app.live_mode = true;
        app.orders = Some(live::spawn(proxy, port));
    }
    event_loop.run_app(&mut app).expect("run app");
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cam() -> FlyCamera {
        FlyCamera {
            pos: Vec3::new(100.0, -200.0, 64.0),
            yaw: 0.7,
            pitch: -0.4,
        }
    }

    /// A click dead centre unprojects to the camera's own view direction — the sanity check that the
    /// NDC mapping and the 0..1 (not -1..1) clip-depth convention are the right way round.
    #[test]
    fn centre_click_rays_along_the_view_direction() {
        let c = cam();
        let (w, h) = (1280.0, 720.0);
        let (from, dir) = c.pick_ray(w / h, (w, h), (w * 0.5, h * 0.5)).expect("ray");
        assert!(
            dir.dot(c.dir()) > 0.9999,
            "centre ray should follow the look direction (dot {})",
            dir.dot(c.dir())
        );
        // The origin sits on the near plane, just ahead of the eye and along that same direction.
        let off = from - c.pos;
        assert!(
            off.length() < 8.0,
            "near-plane origin is ~4u ahead, got {}",
            off.length()
        );
        assert!(
            off.normalize_or_zero().dot(c.dir()) > 0.99,
            "and lies in front of the eye"
        );
    }

    /// Off-centre clicks must lean the correct way: right of centre yaws toward the camera's right,
    /// above centre tilts up. Catches a flipped NDC axis, which a centre-only test can't see.
    #[test]
    fn off_centre_clicks_lean_the_right_way() {
        let c = cam();
        let (w, h) = (800.0, 600.0);
        let fwd = c.dir();
        let right = fwd.cross(Vec3::Z).normalize();
        let ray = |x: f32, y: f32| c.pick_ray(w / h, (w, h), (x, y)).expect("ray").1;

        assert!(ray(w * 0.9, h * 0.5).dot(right) > 0.0, "clicking right leans right");
        assert!(ray(w * 0.1, h * 0.5).dot(right) < 0.0, "clicking left leans left");
        // Window Y grows downward, so the top of the screen must aim higher than the bottom.
        assert!(
            ray(w * 0.5, h * 0.1).z > ray(w * 0.5, h * 0.9).z,
            "the top of the window aims above the bottom"
        );
    }

    /// A zero-sized viewport (a minimised window) has no ray rather than a NaN one.
    #[test]
    fn degenerate_viewport_has_no_ray() {
        assert!(cam().pick_ray(1.0, (0.0, 0.0), (0.0, 0.0)).is_none());
    }
}
