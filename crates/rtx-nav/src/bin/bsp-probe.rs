// SPDX-License-Identifier: AGPL-3.0-or-later

//! Persistent JSONL ground oracle over Quake's standing-player collision hull.

use std::collections::HashMap;
use std::env;
use std::fs;
use std::io::{self, BufRead, BufWriter, Write};
use std::path::{Path, PathBuf};

use glam::Vec3;
use rtx_nav::bsp::Bsp;
use rtx_nav::qphys::ORIGIN_TO_FEET;
use serde::{Deserialize, Serialize};

const TRACE_DOWN: f32 = 2.0;
const MIN_GROUND_NORMAL_Z: f32 = 0.7;
const PLAYER_MINS: Vec3 = Vec3::new(-16.0, -16.0, -24.0);
const PLAYER_MAXS: Vec3 = Vec3::new(16.0, 16.0, 32.0);

#[derive(Deserialize)]
struct Request {
    p: [f32; 3],
}

#[derive(Serialize, Debug)]
struct Response {
    grounded: bool,
    floor_z: f32,
    normal_z: f32,
    contents: i32,
    status: &'static str,
}

struct Probe {
    bsp: Bsp,
    mover_bounds: Vec<(Vec3, Vec3)>,
}

impl Probe {
    fn load(path: &Path) -> Result<Self, String> {
        let bytes = fs::read(path).map_err(|error| format!("read {}: {error}", path.display()))?;
        let bsp =
            Bsp::parse(&bytes).ok_or_else(|| format!("parse {}: unsupported or malformed BSP", path.display()))?;
        let mover_bounds = mover_model_indices(&bsp.entities)
            .into_iter()
            .filter_map(|index| bsp.submodel(index).map(|model| (model.mins, model.maxs)))
            .collect();
        Ok(Self { bsp, mover_bounds })
    }

    fn query(&self, origin: Vec3) -> Response {
        let end = origin - Vec3::Z * TRACE_DOWN;
        let trace = self.bsp.hull1_trace(origin, end);
        let contents = self.bsp.pointcontents(origin);
        let floor_z = trace.endpos.z - ORIGIN_TO_FEET;
        if self.overlaps_mover_sweep(origin, end) {
            return Response {
                grounded: false,
                floor_z,
                normal_z: trace.plane_normal.z,
                contents,
                status: "unknown",
            };
        }
        if trace.start_solid {
            return Response {
                grounded: false,
                floor_z,
                normal_z: trace.plane_normal.z,
                contents,
                status: "solid",
            };
        }
        let hit_distance = origin.z - trace.endpos.z;
        Response {
            grounded: trace.fraction < 1.0 && hit_distance <= TRACE_DOWN && trace.plane_normal.z >= MIN_GROUND_NORMAL_Z,
            floor_z,
            normal_z: trace.plane_normal.z,
            contents,
            status: "ok",
        }
    }

    fn overlaps_mover_sweep(&self, start: Vec3, end: Vec3) -> bool {
        let swept_mins = start.min(end) + PLAYER_MINS;
        let swept_maxs = start.max(end) + PLAYER_MAXS;
        self.mover_bounds.iter().any(|(mins, maxs)| {
            swept_mins.x <= maxs.x
                && swept_maxs.x >= mins.x
                && swept_mins.y <= maxs.y
                && swept_maxs.y >= mins.y
                && swept_mins.z <= maxs.z
                && swept_maxs.z >= mins.z
        })
    }
}

/// Parse just enough of the entity lump to associate moving brush classnames with `*N` models.
fn mover_model_indices(entities: &str) -> Vec<usize> {
    let tokens = quoted_tokens(entities);
    let mut movers = Vec::new();
    let mut current: HashMap<&str, &str> = HashMap::new();
    let mut i = 0;
    while i < tokens.len() {
        match tokens[i] {
            "{" => {
                current.clear();
                i += 1;
            }
            "}" => {
                let classname = current.get("classname").copied().unwrap_or("");
                if matches!(classname, "func_door" | "func_door_secret" | "func_plat" | "func_train") {
                    if let Some(index) = current
                        .get("model")
                        .and_then(|model| model.strip_prefix('*'))
                        .and_then(|number| number.parse().ok())
                    {
                        movers.push(index);
                    }
                }
                current.clear();
                i += 1;
            }
            key if i + 1 < tokens.len() => {
                current.insert(key, tokens[i + 1]);
                i += 2;
            }
            _ => i += 1,
        }
    }
    movers.sort_unstable();
    movers.dedup();
    movers
}

fn quoted_tokens(input: &str) -> Vec<&str> {
    let bytes = input.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'{' => {
                out.push("{");
                i += 1;
            }
            b'}' => {
                out.push("}");
                i += 1;
            }
            b'"' => {
                let start = i + 1;
                i = start;
                while i < bytes.len() && bytes[i] != b'"' {
                    i += 1;
                }
                out.push(&input[start..i]);
                i += usize::from(i < bytes.len());
            }
            _ => i += 1,
        }
    }
    out
}

fn map_arg() -> Result<PathBuf, String> {
    let mut args = env::args_os().skip(1);
    match (args.next(), args.next(), args.next()) {
        (Some(flag), Some(path), None) if flag == "--map" => Ok(path.into()),
        _ => Err("usage: bsp-probe --map <bsp-path>".to_owned()),
    }
}

fn main() -> Result<(), String> {
    let path = map_arg()?;
    let probe = Probe::load(&path)?;
    eprintln!(
        "bsp-probe: loaded {} ({} mover models)",
        path.display(),
        probe.mover_bounds.len()
    );
    let stdin = io::stdin();
    let mut stdout = BufWriter::new(io::stdout().lock());
    serde_json::to_writer(&mut stdout, &serde_json::json!({"v": 1})).map_err(|e| e.to_string())?;
    writeln!(stdout).map_err(|e| e.to_string())?;
    stdout.flush().map_err(|e| e.to_string())?;

    for line in stdin.lock().lines() {
        let response = match line {
            Ok(line) => match serde_json::from_str::<Request>(&line) {
                Ok(request) if request.p.iter().all(|v| v.is_finite()) => probe.query(Vec3::from_array(request.p)),
                Ok(_) | Err(_) => Response {
                    grounded: false,
                    floor_z: 0.0,
                    normal_z: 0.0,
                    contents: 0,
                    status: "error",
                },
            },
            Err(error) => return Err(format!("stdin: {error}")),
        };
        serde_json::to_writer(&mut stdout, &response).map_err(|e| e.to_string())?;
        writeln!(stdout).map_err(|e| e.to_string())?;
        stdout.flush().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const DM3_PATH: &str = "/mnt/c/nQuake/qw/maps/dm3.bsp";
    const DM3_SHA256: &str = "aec9edbb727c0a206edc2c0688775ce8242c0d51e1ee7583c7126c76f7c3b2f1";

    fn pinned_dm3() -> Probe {
        let bytes = fs::read(DM3_PATH)
            .unwrap_or_else(|error| panic!("required pinned fixture {DM3_PATH} is unavailable: {error}"));
        let digest = sha256(&bytes);
        assert_eq!(digest, DM3_SHA256, "dm3 fixture drifted: {DM3_PATH}");
        Probe::load(Path::new(DM3_PATH)).expect("pinned dm3 must parse")
    }

    #[test]
    fn pinned_dm3_acceptance_points_follow_hull_one_origin_semantics() {
        let probe = pinned_dm3();
        for p in [
            [-292.6, 548.2, 120.0],
            [310.1, 670.4, 56.0],
            [336.2, 666.1, 56.0],
            [83.3, 670.0, 40.0],
        ] {
            let response = probe.query(Vec3::from_array(p));
            assert!(response.grounded, "{p:?}: {response:?}");
            assert_eq!(response.status, "ok", "{p:?}: {response:?}");
            let expected_floor = p[2] - ORIGIN_TO_FEET;
            assert!((response.floor_z - expected_floor).abs() <= 1.0, "{p:?}: {response:?}");
        }

        let apex = probe.query(Vec3::new(313.0, 586.0, 99.8));
        assert!(!apex.grounded, "apex: {apex:?}");
    }

    // Small dependency-free SHA-256 used only to fail closed on fixture drift.
    fn sha256(input: &[u8]) -> String {
        use std::fmt::Write as _;
        let mut state = [
            0x6a09e667u32,
            0xbb67ae85,
            0x3c6ef372,
            0xa54ff53a,
            0x510e527f,
            0x9b05688c,
            0x1f83d9ab,
            0x5be0cd19,
        ];
        let k = [
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98,
            0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
            0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8,
            0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
            0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
            0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
            0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
            0xc67178f2,
        ];
        let bit_len = (input.len() as u64) * 8;
        let mut padded = input.to_vec();
        padded.push(0x80);
        while padded.len() % 64 != 56 {
            padded.push(0);
        }
        padded.extend_from_slice(&bit_len.to_be_bytes());
        for chunk in padded.chunks_exact(64) {
            let mut w = [0u32; 64];
            for (i, bytes) in chunk.chunks_exact(4).enumerate() {
                w[i] = u32::from_be_bytes(bytes.try_into().unwrap());
            }
            for i in 16..64 {
                let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
                let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
                w[i] = w[i - 16].wrapping_add(s0).wrapping_add(w[i - 7]).wrapping_add(s1);
            }
            let mut v = state;
            for i in 0..64 {
                let s1 = v[4].rotate_right(6) ^ v[4].rotate_right(11) ^ v[4].rotate_right(25);
                let ch = (v[4] & v[5]) ^ (!v[4] & v[6]);
                let t1 = v[7]
                    .wrapping_add(s1)
                    .wrapping_add(ch)
                    .wrapping_add(k[i])
                    .wrapping_add(w[i]);
                let s0 = v[0].rotate_right(2) ^ v[0].rotate_right(13) ^ v[0].rotate_right(22);
                let maj = (v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]);
                let t2 = s0.wrapping_add(maj);
                v = [
                    t1.wrapping_add(t2),
                    v[0],
                    v[1],
                    v[2],
                    v[3].wrapping_add(t1),
                    v[4],
                    v[5],
                    v[6],
                ];
            }
            for i in 0..8 {
                state[i] = state[i].wrapping_add(v[i]);
            }
        }
        let mut out = String::new();
        for word in state {
            write!(&mut out, "{word:08x}").unwrap();
        }
        out
    }
}
