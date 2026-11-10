//! The "Today's Work" switch: read and changed with the bundled `workbench today`, which keeps the
//! setting in ~/.workbench/config.toml. The server reads it when it starts.

use std::path::Path;
use std::process::{Command, Stdio};

/// Whether Today's Work is on, as `workbench today` reports it.
pub fn is_on(binary: &Path) -> Result<bool, String> {
    let answer = run(binary, None)?;
    match answer.trim() {
        "on" => Ok(true),
        "off" => Ok(false),
        other => Err(format!("unexpected answer from `workbench today`: {other}")),
    }
}

/// Turn Today's Work on or off in config.toml; the server has to be restarted afterwards.
pub fn set(binary: &Path, on: bool) -> Result<(), String> {
    run(binary, Some(if on { "on" } else { "off" })).map(|_| ())
}

/// Run `workbench today [arg]` and return what it printed, or why it failed.
fn run(binary: &Path, arg: Option<&str>) -> Result<String, String> {
    let out = Command::new(binary)
        .arg("today")
        .args(arg)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("cannot run {}: {e}", binary.display()))?;
    if out.status.success() {
        return Ok(String::from_utf8_lossy(&out.stdout).into_owned());
    }
    let stderr = String::from_utf8_lossy(&out.stderr);
    // The CLI frames its usage errors in a box; keep the last line that says something
    let reason = stderr
        .lines()
        .rev()
        .map(|line| line.trim_matches(|c: char| c.is_whitespace() || "│╭╰─╮╯".contains(c)))
        .find(|line| line.chars().any(char::is_alphanumeric));
    Err(match (reason, out.status.code()) {
        (Some(line), _) => line.to_string(),
        (None, Some(code)) => format!("`workbench today` exited with code {code}"),
        (None, None) => "`workbench today` was stopped".into(),
    })
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::path::PathBuf;

    /// A stand-in for the bundled binary: keeps the setting in a file next to itself and, like
    /// the real `workbench today`, prints `on` or `off` when asked and a sentence when changed.
    const FAKE_CLI: &str = r#"#!/bin/sh
state="$(dirname "$0")/state"
[ "$1" = today ] || { echo "No such command '$1'." >&2; exit 2; }
case "$2" in
  "") if [ -f "$state" ]; then cat "$state"; else echo on; fi ;;
  on|off)
    if [ -f "$(dirname "$0")/read-only" ]; then
      echo "╭─ Error ─╮" >&2; echo "│ cannot write config.toml │" >&2; echo "╰─────────╯" >&2
      exit 1
    fi
    echo "$2" > "$state"; echo "Today's Work is $2. Restart the dashboard server." ;;
  *) echo "Usage: workbench today [on|off]" >&2; exit 2 ;;
esac
"#;

    fn fake_cli(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("workbench-today-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let binary = dir.join("workbench");
        std::fs::write(&binary, FAKE_CLI).unwrap();
        std::fs::set_permissions(&binary, std::fs::Permissions::from_mode(0o755)).unwrap();
        binary
    }

    #[test]
    fn reads_and_changes_the_setting_through_the_cli() {
        let binary = fake_cli("switch");
        assert_eq!(is_on(&binary), Ok(true));
        assert_eq!(set(&binary, false), Ok(()));
        assert_eq!(is_on(&binary), Ok(false));
        assert_eq!(set(&binary, true), Ok(()));
        assert_eq!(is_on(&binary), Ok(true));
        let _ = std::fs::remove_dir_all(binary.parent().unwrap());
    }

    #[test]
    fn a_failed_command_reports_its_last_readable_line() {
        let binary = fake_cli("failure");
        std::fs::write(binary.parent().unwrap().join("read-only"), "").unwrap();
        assert_eq!(set(&binary, false), Err("cannot write config.toml".into()));
        assert_eq!(is_on(&binary), Ok(true));

        std::fs::write(binary.parent().unwrap().join("state"), "maybe\n").unwrap();
        assert!(is_on(&binary).unwrap_err().contains("unexpected answer"));
        let _ = std::fs::remove_dir_all(binary.parent().unwrap());
    }

    #[test]
    fn a_missing_binary_is_an_error() {
        let missing = std::env::temp_dir().join("workbench-today-no-such-binary");
        assert!(is_on(&missing).unwrap_err().starts_with("cannot run "));
        assert!(set(&missing, true).is_err());
    }
}
