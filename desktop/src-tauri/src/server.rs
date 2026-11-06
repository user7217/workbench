//! Runs the bundled `workbench serve` as a child process and keeps it running.
//!
//! The server listens on 127.0.0.1 only, so nothing outside this computer can reach it. A server
//! that crashes is restarted; one that fails three times within 30 seconds of starting is left
//! stopped (with its last log line as the reason) until Restart is chosen from the menu.

use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

pub const PORT: u16 = 8420;
const QUICK_FAILURE_WINDOW: Duration = Duration::from_secs(30);
const MAX_QUICK_FAILURES: u32 = 3;

#[derive(Clone, Debug, PartialEq)]
pub enum Status {
    Starting,
    Running,
    /// Another Workbench server (started outside the app) already answers on the port
    RunningElsewhere,
    PortBusy,
    Stopped(String),
}

impl Status {
    pub fn label(&self) -> String {
        match self {
            Status::Starting => "Starting…".into(),
            Status::Running => format!("Running on localhost:{PORT}"),
            Status::RunningElsewhere => {
                format!("Running on localhost:{PORT} (started outside the app)")
            }
            Status::PortBusy => format!("Port {PORT} is used by another program"),
            Status::Stopped(why) => format!("Stopped: {why}"),
        }
    }

    pub fn dashboard_available(&self) -> bool {
        matches!(self, Status::Running | Status::RunningElsewhere)
    }
}

pub fn dashboard_url() -> String {
    format!("http://localhost:{PORT}/")
}

pub struct Supervisor {
    binary: PathBuf,
    log_path: PathBuf,
    home: PathBuf,
    path_env: Mutex<String>,
    child: Mutex<Option<Child>>,
    last_line: Arc<Mutex<String>>,
    quitting: AtomicBool,
    restart_requested: AtomicBool,
    wake: (Mutex<bool>, Condvar),
}

impl Supervisor {
    pub fn new(binary: PathBuf, log_path: PathBuf, home: PathBuf) -> Arc<Self> {
        Arc::new(Self {
            binary,
            log_path,
            home,
            path_env: Mutex::new(String::new()),
            child: Mutex::new(None),
            last_line: Arc::new(Mutex::new(String::new())),
            quitting: AtomicBool::new(false),
            restart_requested: AtomicBool::new(false),
            wake: (Mutex::new(false), Condvar::new()),
        })
    }

    /// Start the supervising thread; `on_status` is called whenever the status changes.
    pub fn start(self: &Arc<Self>, on_status: impl Fn(Status) + Send + 'static) {
        let me = Arc::clone(self);
        thread::spawn(move || me.run(on_status));
    }

    /// Stop the server (if the app started it) and start it again.
    pub fn restart(&self) {
        self.restart_requested.store(true, Ordering::SeqCst);
        self.stop_child();
        self.notify();
    }

    /// Stop the server for good; called when the app quits.
    pub fn shutdown(&self) {
        self.quitting.store(true, Ordering::SeqCst);
        self.stop_child();
        self.notify();
    }

    fn run(&self, on_status: impl Fn(Status)) {
        // Runs a login shell, so it happens here rather than before the menu appears
        *self.path_env.lock().unwrap() = crate::environment::tool_path(&self.home);
        let mut quick_failures = 0;
        while !self.quitting.load(Ordering::SeqCst) {
            on_status(Status::Starting);
            if port_open() {
                // Something already listens on the port: a Workbench server started by hand (or
                // left behind) still gives a working dashboard; anything else blocks the port.
                on_status(if is_workbench_server() {
                    Status::RunningElsewhere
                } else {
                    Status::PortBusy
                });
                self.wait(Some(Duration::from_secs(5)));
                continue;
            }
            // A Restart chosen while stopped has done its job by getting here
            self.restart_requested.store(false, Ordering::SeqCst);
            if let Err(err) = self.spawn() {
                on_status(Status::Stopped(err));
                self.wait(None);
                continue;
            }
            let started = Instant::now();
            let mut announced = false;
            let exit = loop {
                thread::sleep(Duration::from_millis(300));
                let mut guard = self.child.lock().unwrap();
                match guard.as_mut().map(|c| c.try_wait()) {
                    Some(Ok(None)) => {}
                    Some(Ok(Some(status))) => {
                        guard.take();
                        break Some(status);
                    }
                    // Taken by stop_child (restart or quit), or the wait failed
                    _ => {
                        guard.take();
                        break None;
                    }
                }
                drop(guard);
                if !announced && port_open() {
                    announced = true;
                    on_status(Status::Running);
                }
            };
            if self.quitting.load(Ordering::SeqCst) {
                break;
            }
            if self.restart_requested.swap(false, Ordering::SeqCst) {
                quick_failures = 0;
                continue;
            }
            if started.elapsed() < QUICK_FAILURE_WINDOW {
                quick_failures += 1;
            } else {
                quick_failures = 0;
            }
            let why = self.reason(exit);
            if quick_failures >= MAX_QUICK_FAILURES {
                on_status(Status::Stopped(why));
                self.wait(None);
                quick_failures = 0;
            } else {
                on_status(Status::Stopped(format!("{why} (restarting)")));
                self.wait(Some(Duration::from_secs(2)));
            }
        }
    }

    fn spawn(&self) -> Result<(), String> {
        if !self.binary.is_file() {
            return Err(format!(
                "server binary missing at {}",
                self.binary.display()
            ));
        }
        if let Some(dir) = self.log_path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let log = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.log_path)
            .map_err(|e| format!("cannot open log: {e}"))?;
        let stdout_log = log.try_clone().map_err(|e| e.to_string())?;
        let mut child = Command::new(&self.binary)
            .args([
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                &PORT.to_string(),
                "--exit-with-parent",
            ])
            .env("PATH", self.path_env.lock().unwrap().as_str())
            .stdin(Stdio::null())
            .stdout(Stdio::from(stdout_log))
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("cannot start server: {e}"))?;

        // Copy stderr (uvicorn logs and errors) to the log, remembering the last readable line
        // so a failed start can say why in the menu.
        if let Some(stderr) = child.stderr.take() {
            let last_line = Arc::clone(&self.last_line);
            let mut log = log;
            thread::spawn(move || {
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    let _ = writeln!(log, "{line}");
                    let text = line
                        .trim_matches(|c: char| c.is_whitespace() || "│╭╰─╮╯".contains(c))
                        .to_string();
                    if text.chars().any(char::is_alphanumeric) {
                        *last_line.lock().unwrap() = text;
                    }
                }
            });
        }
        self.last_line.lock().unwrap().clear();
        *self.child.lock().unwrap() = Some(child);
        Ok(())
    }

    fn reason(&self, exit: Option<std::process::ExitStatus>) -> String {
        let line = self.last_line.lock().unwrap().clone();
        let mut why = if !line.is_empty() {
            line.trim_start_matches("Error: ").to_string()
        } else {
            match exit.and_then(|s| s.code()) {
                Some(code) => format!("server exited with code {code}"),
                None => "server stopped".into(),
            }
        };
        if why.chars().count() > 90 {
            why = why.chars().take(89).collect::<String>() + "…";
        }
        why
    }

    /// Ask the server to shut down (SIGTERM), then force it after 5 seconds.
    fn stop_child(&self) {
        let Some(mut child) = self.child.lock().unwrap().take() else {
            return;
        };
        #[cfg(unix)]
        unsafe {
            libc::kill(child.id() as i32, libc::SIGTERM);
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
        let _ = child.kill();
        let _ = child.wait();
    }

    fn wait(&self, timeout: Option<Duration>) {
        let (lock, cvar) = &self.wake;
        let mut woken = lock.lock().unwrap();
        if !*woken {
            woken = match timeout {
                Some(t) => cvar.wait_timeout(woken, t).unwrap().0,
                None => cvar.wait(woken).unwrap(),
            };
        }
        *woken = false;
    }

    fn notify(&self) {
        let (lock, cvar) = &self.wake;
        *lock.lock().unwrap() = true;
        cvar.notify_all();
    }
}

fn addr() -> SocketAddr {
    SocketAddr::from(([127, 0, 0, 1], PORT))
}

fn port_open() -> bool {
    TcpStream::connect_timeout(&addr(), Duration::from_millis(300)).is_ok()
}

/// True when the program on the port answers /api/status like a Workbench server.
fn is_workbench_server() -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(&addr(), Duration::from_millis(500)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let request = format!("GET /api/status HTTP/1.0\r\nHost: 127.0.0.1:{PORT}\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut body = String::new();
    let _ = stream.take(64 * 1024).read_to_string(&mut body);
    body.contains("\"last_good_commit\"")
}
