#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::Manager;
use tauri_plugin_shell::ShellExt;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            #[cfg(all(desktop, not(debug_assertions)))]
            {
                let sidecar = app
                    .shell()
                    .sidecar("student-ai-checker-backend")?
                    .args(["--host", "127.0.0.1", "--port", "8765", "--no-browser"]);
                let (_receiver, _child) = sidecar.spawn()?;
            }
            let _ = app.get_webview_window("main");
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Student AI Checker");
}
