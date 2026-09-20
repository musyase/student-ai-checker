#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::CommandChild;
#[cfg(all(desktop, not(debug_assertions)))]
use tauri_plugin_shell::ShellExt;

struct BackendProcess {
    child: Mutex<Option<CommandChild>>,
    pid: u32,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(
            tauri::plugin::Builder::<_, ()>::new("backend-process")
                .setup(|app, _api| {
                    #[cfg(not(all(desktop, not(debug_assertions))))]
                    let _ = app;

                    #[cfg(all(desktop, not(debug_assertions)))]
                    {
                        let sidecar = app
                            .shell()
                            .sidecar("student-ai-checker-backend")?
                            .args(["--host", "127.0.0.1", "--port", "8765", "--no-browser"]);
                        let (_receiver, child) = sidecar.spawn()?;
                        app.manage(BackendProcess {
                            pid: child.pid(),
                            child: Mutex::new(Some(child)),
                        });
                    }
                    Ok(())
                })
                .on_event(|app, event| {
                    if let RunEvent::Exit = event {
                        if let Some(process) = app.try_state::<BackendProcess>() {
                            if let Ok(mut child) = process.child.lock() {
                                #[cfg(windows)]
                                {
                                    let _ = std::process::Command::new("taskkill")
                                        .args(["/PID", &process.pid.to_string(), "/T", "/F"])
                                        .status();
                                }

                                #[cfg(unix)]
                                {
                                    let _ = std::process::Command::new("pkill")
                                        .args(["-TERM", "-P", &process.pid.to_string()])
                                        .status();
                                }

                                if let Some(child) = child.take() {
                                    let _ = child.kill();
                                }
                            }
                        }
                    }
                })
                .build(),
        )
        .setup(|app| {
            let _ = app.get_webview_window("main");
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Student AI Checker");
}
