//! Paths and environment the bundled server needs when started from the menu bar.

use std::path::{Path, PathBuf};
use std::process::Command;

/// The PATH of a login shell, plus the usual tool folders.
///
/// Apps started from Finder or at login get a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin), so
/// tools the server may call (git from Homebrew, the claude CLI for the privacy review) would
/// not be found. The login shell's PATH is what a terminal would have.
pub fn tool_path(home: &Path) -> String {
    let mut dirs: Vec<String> = Vec::new();
    if let Some(login) = login_shell_path() {
        dirs.extend(login.split(':').map(str::to_string));
    }
    if let Ok(current) = std::env::var("PATH") {
        dirs.extend(current.split(':').map(str::to_string));
    }
    for extra in [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ] {
        dirs.push(extra.into());
    }
    dirs.push(home.join(".local/bin").to_string_lossy().into_owned());

    let mut seen = std::collections::HashSet::new();
    dirs.retain(|d| !d.is_empty() && seen.insert(d.clone()));
    dirs.join(":")
}

/// PATH as a login shell sets it (what a new terminal gets), if the shell can be run.
pub fn login_shell_path() -> Option<String> {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".into());
    let out = Command::new(&shell)
        .args(["-l", "-c", "printf %s \"$PATH\""])
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).into_owned())
}

/// ~/.workbench/logs/desktop-server.log: the server's output when run by the app.
pub fn server_log(home: &Path) -> PathBuf {
    home.join(".workbench")
        .join("logs")
        .join("desktop-server.log")
}
