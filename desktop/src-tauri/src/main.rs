//! Workbench desktop app: a menu-bar icon that runs the dashboard server on this computer.
//!
//! The server is the bundled `workbench serve`, listening on 127.0.0.1 only. The dashboard opens
//! in the default browser. The app starts at login (a LaunchAgent on macOS) and has no window.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod cli_link;
mod environment;
mod server;
mod updater;

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, Wry};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_opener::OpenerExt;

use cli_link::LinkState;
use server::{Status, Supervisor};
use updater::Updates;

/// Passed by the login item, so a start at login does not open the browser
const AUTOSTART_ARG: &str = "--autostart";

struct AppState {
    supervisor: Arc<Supervisor>,
    binary: PathBuf,
    home: PathBuf,
    login_item: CheckMenuItem<Wry>,
    updates: Arc<Updates>,
}

fn main() {
    let app = tauri::Builder::default()
        // Opening the app again while it runs just opens the dashboard
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            open_dashboard(app)
        }))
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec![AUTOSTART_ARG]),
        ))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| Ok(setup(app)?))
        .build(tauri::generate_context!())
        .expect("failed to build the Workbench app");

    app.run(|app, event| {
        if let RunEvent::Exit = event {
            app.state::<AppState>().supervisor.shutdown();
        }
    });
}

fn setup(app: &mut tauri::App) -> tauri::Result<()> {
    #[cfg(target_os = "macos")]
    app.set_activation_policy(tauri::ActivationPolicy::Accessory);

    let handle = app.handle().clone();
    let home = app.path().home_dir()?;
    let binary = app
        .path()
        .resource_dir()?
        .join("workbench")
        .join("workbench");
    let supervisor = Supervisor::new(binary.clone(), environment::server_log(&home), home.clone());

    // Start at login is on after the first launch; afterwards the menu item decides
    let first_run_marker = app.path().app_config_dir()?.join("login-item-set");
    if !first_run_marker.exists() {
        let _ = app.autolaunch().enable();
        if let Some(dir) = first_run_marker.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let _ = std::fs::write(&first_run_marker, "");
    }
    let at_login = app.autolaunch().is_enabled().unwrap_or(false);

    let status_item =
        MenuItem::with_id(app, "status", Status::Starting.label(), false, None::<&str>)?;
    let open_item = MenuItem::with_id(app, "open", "Open Dashboard", false, None::<&str>)?;
    let restart_item = MenuItem::with_id(app, "restart", "Restart Server", true, None::<&str>)?;
    let login_item =
        CheckMenuItem::with_id(app, "login", "Start at Login", true, at_login, None::<&str>)?;
    let cli_item = MenuItem::with_id(app, "cli", "Install Command Line Tool…", true, None::<&str>)?;
    let log_item = MenuItem::with_id(app, "log", "Show Server Log", true, None::<&str>)?;
    let update_item = MenuItem::with_id(
        app,
        "update",
        Updates::menu_item_label(),
        true,
        None::<&str>,
    )?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit Workbench", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &status_item,
            &PredefinedMenuItem::separator(app)?,
            &open_item,
            &restart_item,
            &PredefinedMenuItem::separator(app)?,
            &login_item,
            &cli_item,
            &log_item,
            &PredefinedMenuItem::separator(app)?,
            &update_item,
            &quit_item,
        ],
    )?;

    let tray_icon = tauri::image::Image::from_bytes(include_bytes!("../icons/tray.png"))?;
    TrayIconBuilder::with_id("workbench")
        .icon(tray_icon)
        .icon_as_template(true)
        .tooltip("Workbench")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| handle_menu(app, event.id.as_ref()))
        .build(app)?;

    let updates = Updates::new(update_item, updater::log_path(&handle));
    updates.start_background_checks(&handle);

    app.manage(AppState {
        supervisor: Arc::clone(&supervisor),
        binary,
        home,
        login_item,
        updates,
    });

    // Opened by hand: show the dashboard once it is up. Started at login: stay in the menu bar.
    let opened = AtomicBool::new(std::env::args().any(|a| a == AUTOSTART_ARG));
    supervisor.start(move |status| {
        let _ = status_item.set_text(status.label());
        let _ = open_item.set_enabled(status.dashboard_available());
        if status.dashboard_available() && !opened.swap(true, Ordering::SeqCst) {
            open_dashboard(&handle);
        }
    });
    Ok(())
}

fn handle_menu(app: &AppHandle, id: &str) {
    let state = app.state::<AppState>();
    match id {
        "open" => open_dashboard(app),
        "restart" => {
            // Stopping waits for the server to exit, so keep it off the main thread
            let supervisor = Arc::clone(&state.supervisor);
            std::thread::spawn(move || supervisor.restart());
        }
        "login" => {
            let launcher = app.autolaunch();
            let enable = !launcher.is_enabled().unwrap_or(false);
            let _ = if enable {
                launcher.enable()
            } else {
                launcher.disable()
            };
            let _ = state
                .login_item
                .set_checked(launcher.is_enabled().unwrap_or(enable));
        }
        "cli" => install_cli(app),
        "update" => state
            .updates
            .on_menu_click(app, Arc::clone(&state.supervisor)),
        "log" => {
            let log = environment::server_log(&state.home);
            if !log.exists() {
                let _ = std::fs::create_dir_all(log.parent().unwrap());
                let _ = std::fs::write(&log, "");
            }
            let _ = app.opener().open_path(log.to_string_lossy(), None::<&str>);
        }
        "quit" => app.exit(0),
        _ => {}
    }
}

fn open_dashboard(app: &AppHandle) {
    let _ = app.opener().open_url(server::dashboard_url(), None::<&str>);
}

fn install_cli(app: &AppHandle) {
    let state = app.state::<AppState>();
    let link = cli_link::link_path(&state.home);
    let binary = state.binary.clone();
    let handle = app.clone();
    let link_for_install = link.clone();
    let install = move || {
        let message = match cli_link::install(&link_for_install, &binary) {
            Ok(()) => {
                let dir = link_for_install.parent().unwrap();
                let on_path = environment::login_shell_path()
                    .map(|p| cli_link::on_path(dir, &p))
                    .unwrap_or(true);
                if on_path {
                    format!(
                        "Installed. `workbench` in a terminal and in agent hooks now runs the copy inside this app ({}).",
                        link_for_install.display()
                    )
                } else {
                    format!(
                        "Installed at {}, but {} is not on the shell PATH. Add it to the PATH to use `workbench` in a terminal.",
                        link_for_install.display(),
                        dir.display()
                    )
                }
            }
            Err(err) => format!("Could not install the command line tool: {err}"),
        };
        handle
            .dialog()
            .message(message)
            .title("Workbench")
            .kind(MessageDialogKind::Info)
            .show(|_| {});
    };

    match cli_link::state(&link) {
        // Finding the shell PATH runs a shell, so do the work off the main thread
        LinkState::Missing | LinkState::App => {
            std::thread::spawn(install);
        }
        LinkState::Other(target) => {
            app.dialog()
                .message(format!(
                    "{} already exists (it points to {}).\n\nReplace it with the workbench inside this app? Agent hooks and the terminal would then use the app's copy.",
                    link.display(),
                    target.display()
                ))
                .title("Replace the workbench command?")
                .kind(MessageDialogKind::Warning)
                .buttons(MessageDialogButtons::OkCancelCustom("Replace".into(), "Cancel".into()))
                .show(move |replace| {
                    if replace {
                        std::thread::spawn(install);
                    }
                });
        }
    }
}
