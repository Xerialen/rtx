use std::path::PathBuf;
use std::process::Command;

fn main() {
    let manifest = PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let repo = manifest.parent().and_then(|path| path.parent()).unwrap();
    let git_dir = repo.join(".git");
    let head = git_dir.join("HEAD");
    println!("cargo:rerun-if-changed={}", head.display());
    if let Ok(value) = std::fs::read_to_string(&head) {
        if let Some(reference) = value.trim().strip_prefix("ref: ") {
            println!("cargo:rerun-if-changed={}", git_dir.join(reference).display());
        }
    }
    let commit = Command::new("git")
        .args(["-C", repo.to_str().unwrap(), "rev-parse", "HEAD"])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .map(|value| value.trim().to_owned())
        .unwrap_or_else(|| "unknown".to_owned());
    println!("cargo:rustc-env=BSP_PROBE_COMMIT={commit}");
}
