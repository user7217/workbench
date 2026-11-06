//! "Install Command Line Tool": links ~/.local/bin/workbench to the binary inside the app, so the
//! coding-agent hooks and the terminal use the same workbench as the app's server.

use std::io;
use std::path::{Path, PathBuf};

/// Where the bundled binary sits inside any Workbench.app
const BUNDLED_SUFFIX: &str = "Contents/Resources/workbench/workbench";

pub enum LinkState {
    Missing,
    /// Already points into a Workbench.app (this one or an older copy)
    App,
    /// A workbench installed some other way, such as `uv tool install`
    Other(PathBuf),
}

pub fn link_path(home: &Path) -> PathBuf {
    home.join(".local").join("bin").join("workbench")
}

pub fn state(link: &Path) -> LinkState {
    match std::fs::symlink_metadata(link) {
        Err(_) => LinkState::Missing,
        Ok(_) => match std::fs::read_link(link) {
            Ok(target) if target.ends_with(BUNDLED_SUFFIX) => LinkState::App,
            Ok(target) => LinkState::Other(target),
            Err(_) => LinkState::Other(link.to_path_buf()),
        },
    }
}

/// Create (or replace) the link to the app's binary.
pub fn install(link: &Path, binary: &Path) -> io::Result<()> {
    if let Some(dir) = link.parent() {
        std::fs::create_dir_all(dir)?;
    }
    if std::fs::symlink_metadata(link).is_ok() {
        std::fs::remove_file(link)?;
    }
    #[cfg(unix)]
    std::os::unix::fs::symlink(binary, link)?;
    Ok(())
}

/// True when `dir` is on the given PATH value.
pub fn on_path(dir: &Path, path_env: &str) -> bool {
    path_env.split(':').any(|d| Path::new(d) == dir)
}
