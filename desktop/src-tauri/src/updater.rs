//! Self-update from GitHub Releases (tauri-plugin-updater).
//!
//! Each release publishes `latest.json`, which names the new version and links a signed
//! `Workbench.app` archive. The app checks it a minute after launch and then every 6 hours. A
//! newer version is downloaded, its signature checked against the public key in tauri.conf.json,
//! and installed over this app in place; the menu then offers "Restart to Update". Until then the
//! running app (and its server) stay on the old version, and the next launch or login runs the
//! new one. Pre-releases are never offered: `releases/latest` skips them.

use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tauri::menu::MenuItem;
use tauri::{AppHandle, Manager, Wry};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

use crate::server::Supervisor;

const FIRST_CHECK_DELAY: Duration = Duration::from_secs(60);
const CHECK_INTERVAL: Duration = Duration::from_secs(6 * 60 * 60);
const CHECK_LABEL: &str = "Check for Updates…";

pub struct Updates {
    item: MenuItem<Wry>,
    log: PathBuf,
    busy: AtomicBool,
    /// Version installed on disk but not running yet
    installed: Mutex<Option<String>>,
}

impl Updates {
    pub fn new(item: MenuItem<Wry>, log: PathBuf) -> Arc<Self> {
        Arc::new(Self {
            item,
            log,
            busy: AtomicBool::new(false),
            installed: Mutex::new(None),
        })
    }

    pub fn menu_item_label() -> &'static str {
        CHECK_LABEL
    }

    /// Periodic checks in the background. Builds from `tauri dev` never update themselves.
    pub fn start_background_checks(self: &Arc<Self>, app: &AppHandle) {
        if cfg!(debug_assertions) {
            return;
        }
        let updates = Arc::clone(self);
        let app = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(FIRST_CHECK_DELAY);
            loop {
                if updates.installed.lock().unwrap().is_some() {
                    return; // nothing newer to do until the app restarts
                }
                updates.check(&app, false);
                std::thread::sleep(CHECK_INTERVAL);
            }
        });
    }

    /// The menu item: restart into an installed update, or check now and report the result.
    pub fn on_menu_click(self: &Arc<Self>, app: &AppHandle, supervisor: Arc<Supervisor>) {
        if self.installed.lock().unwrap().is_some() {
            restart(app.clone(), supervisor);
            return;
        }
        let updates = Arc::clone(self);
        let app = app.clone();
        std::thread::spawn(move || {
            if updates.check(&app, true) {
                let version = updates
                    .installed
                    .lock()
                    .unwrap()
                    .clone()
                    .unwrap_or_default();
                let handle = app.clone();
                app.dialog()
                    .message(format!(
                        "Workbench {version} is installed. Restart now to use it? The dashboard is unavailable for a few seconds."
                    ))
                    .title("Update Installed")
                    .kind(MessageDialogKind::Info)
                    .buttons(MessageDialogButtons::OkCancelCustom(
                        "Restart Now".into(),
                        "Later".into(),
                    ))
                    .show(move |now| {
                        if now {
                            restart(handle.clone(), supervisor);
                        }
                    });
            }
        });
    }

    /// Check, download and install. Returns true if an update was installed by this call.
    /// `manual` reports "up to date" and errors in a dialog; background checks only log them.
    fn check(&self, app: &AppHandle, manual: bool) -> bool {
        if self.busy.swap(true, Ordering::SeqCst) {
            return false;
        }
        let current = app.package_info().version.to_string();
        let _ = self.item.set_enabled(false);
        let _ = self.item.set_text("Checking for Updates…");

        let result = tauri::async_runtime::block_on(async {
            let Some(update) = app.updater()?.check().await? else {
                return Ok::<_, tauri_plugin_updater::Error>(None);
            };
            let version = update.version.clone();
            self.log(&format!("downloading {version} (running {current})"));
            let _ = self
                .item
                .set_text(format!("Downloading Workbench {version}…"));
            update.download_and_install(|_, _| {}, || {}).await?;
            Ok(Some(version))
        });

        let installed = match result {
            Ok(Some(version)) => {
                self.log(&format!("installed {version}; restart to run it"));
                let _ = self
                    .item
                    .set_text(format!("Restart to Update to {version}"));
                *self.installed.lock().unwrap() = Some(version);
                true
            }
            Ok(None) => {
                self.log(&format!("up to date ({current})"));
                let _ = self.item.set_text(CHECK_LABEL);
                if manual {
                    show(
                        app,
                        "No Update",
                        &format!("Workbench {current} is the latest version."),
                    );
                }
                false
            }
            Err(err) => {
                self.log(&format!("update failed: {err}"));
                let _ = self.item.set_text(CHECK_LABEL);
                if manual {
                    show(
                        app,
                        "Update Failed",
                        &format!("Could not update Workbench: {err}"),
                    );
                }
                false
            }
        };
        let _ = self.item.set_enabled(true);
        self.busy.store(false, Ordering::SeqCst);
        installed
    }

    fn log(&self, message: &str) {
        if let Some(dir) = self.log.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&self.log) {
            let _ = writeln!(file, "{} {message}", utc_now());
        }
    }
}

/// Stop the server first, so the new version finds the port free, then relaunch the app.
fn restart(app: AppHandle, supervisor: Arc<Supervisor>) {
    std::thread::spawn(move || {
        supervisor.shutdown();
        app.restart();
    });
}

fn show(app: &AppHandle, title: &str, message: &str) {
    app.dialog()
        .message(message)
        .title(title)
        .kind(MessageDialogKind::Info)
        .show(|_| {});
}

/// ~/.workbench/logs/desktop-updater.log
pub fn log_path(app: &AppHandle) -> PathBuf {
    let home = app.path().home_dir().unwrap_or_default();
    home.join(".workbench")
        .join("logs")
        .join("desktop-updater.log")
}

/// "2026-11-07 03:41:00Z" without a date-time dependency (days-to-civil, H. Hinnant)
fn utc_now() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02} {:02}:{:02}:{:02}Z",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}
