#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use gpui_kit::base::StyledExt;
use gpui_kit::component::{
    ActiveTheme, Disableable, Sizable, Theme, button::*, checkbox::Checkbox,
};
use gpui_kit::*;
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Command, Stdio},
    sync::mpsc::{self, Receiver, Sender},
    time::Duration,
};

const PUBLIC_KEY: &str = include_str!("../../compatibility/public-key.pem");

#[cfg(feature = "packaged-engine")]
mod packaged;

#[cfg(feature = "packaged-engine")]
fn repository_root() -> PathBuf {
    std::env::current_exe()
        .expect("cannot locate the patcher executable")
        .parent()
        .expect("executable must have a parent")
        .to_path_buf()
}

#[cfg(not(feature = "packaged-engine"))]
fn repository_root() -> PathBuf {
    if let Some(path) = std::env::var_os("ASIDE_PATCHER_ROOT") {
        return PathBuf::from(path);
    }
    if let Ok(executable) = std::env::current_exe()
        && let Some(root) = executable.ancestors().skip(1).find(|directory| {
            directory.join("pyproject.toml").is_file()
                && directory.join("aside_patcher/protocol.py").is_file()
        })
    {
        return root.to_path_buf();
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("desktop crate must have a repository parent")
        .to_path_buf()
}

struct Patcher {
    repository: PathBuf,
    installation_override: Option<String>,
    data_root_override: Option<String>,
    sender: Sender<Value>,
    receiver: Receiver<Value>,
    state: Value,
    selected: BTreeSet<String>,
    preview: Option<Value>,
    busy: bool,
    pending: String,
    status: String,
    diagnostics: Vec<String>,
    show_diagnostics: bool,
    retry: Option<String>,
}

fn engine(repository: PathBuf, request: Value, sender: Sender<Value>) {
    let run = || -> Result<(), String> {
        #[cfg(feature = "packaged-engine")]
        let mut command = Command::new(packaged::engine_path()?);
        #[cfg(not(feature = "packaged-engine"))]
        let mut command = {
            let mut command = Command::new("uv");
            command.args([
                "run", "--extra", "catalog", "python", "-m", "aside_patcher.protocol",
            ]);
            command
        };
        command
            .env("ASIDE_PATCHER_PUBLIC_KEY", PUBLIC_KEY)
            .current_dir(repository)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("Cannot start Python engine: {e}"))?;
        writeln!(child.stdin.take().unwrap(), "{request}").map_err(|e| e.to_string())?;
        // Drain stderr concurrently so verbose failures cannot deadlock the protocol pipe.
        let stderr = child.stderr.take().unwrap();
        let error_reader = std::thread::spawn(move || {
            BufReader::new(stderr)
                .lines()
                .map_while(Result::ok)
                .collect::<Vec<_>>()
                .join("\n")
        });
        let mut received_result = false;
        for line in BufReader::new(child.stdout.take().unwrap()).lines() {
            let line = line.map_err(|e| e.to_string())?;
            let value: Value =
                serde_json::from_str(&line).map_err(|e| format!("Invalid engine response: {e}"))?;
            received_result |= value["type"] == "result";
            sender.send(value).map_err(|e| e.to_string())?;
        }
        let status = child.wait().map_err(|e| e.to_string())?;
        let errors = error_reader.join().unwrap_or_default();
        if !received_result {
            return Err(format!("Engine exited {status}: {errors}"));
        }
        Ok(())
    };
    if let Err(error) = run() {
        let _ = sender.send(json!({"type":"result", "ok":false, "error":error}));
    }
}

impl Patcher {
    fn new(cx: &mut Context<Self>) -> Self {
        let (sender, receiver) = mpsc::channel();
        let repository = repository_root();
        let mut view = Self {
            repository,
            installation_override: std::env::var("ASIDE_PATCHER_INSTALLATION").ok(),
            data_root_override: std::env::var("ASIDE_PATCHER_DATA_ROOT").ok(),
            sender,
            receiver,
            state: Value::Null,
            selected: BTreeSet::new(),
            preview: None,
            busy: false,
            pending: String::new(),
            status: "Finding your Aside installation…".into(),
            diagnostics: Vec::new(),
            show_diagnostics: false,
            retry: None,
        };
        view.request("refresh", cx);
        cx.spawn(async move |this, cx| {
            loop {
                cx.background_executor()
                    .timer(Duration::from_millis(100))
                    .await;
                if this.update(cx, |view, cx| view.receive(cx)).is_err() {
                    break;
                }
            }
        })
        .detach();
        view
    }

    fn request(&mut self, operation: &str, cx: &mut Context<Self>) {
        if self.busy {
            return;
        }
        self.busy = true;
        self.retry = None;
        self.pending = operation.to_string();
        self.status = match operation {
            "preview" => "Preparing and verifying your selection…",
            "apply" | "restore" => "Applying your verified selection…",
            "refresh" => "Checking approved compatibility updates…",
            _ => "Checking your Aside installation…",
        }
        .into();
        let mut request =
            json!({"protocol_version":1,"operation":operation,"targets":self.selected});
        if let Some(dll) = self
            .installation_override
            .as_deref()
            .or_else(|| self.state["installation"]["dll"].as_str())
        {
            request["installation"] = json!(dll);
        }
        let detected_data_root = (operation != "inspect")
            .then(|| self.state["data_root"].as_str())
            .flatten();
        if let Some(data_root) = self.data_root_override.as_deref().or(detected_data_root) {
            request["data_root"] = json!(data_root);
        }
        if let Some(preview) = &self.preview {
            request["preview_token"] = preview["token"].clone();
        }
        let (repository, sender) = (self.repository.clone(), self.sender.clone());
        std::thread::spawn(move || engine(repository, request, sender));
        cx.notify();
    }

    fn receive(&mut self, cx: &mut Context<Self>) {
        while let Ok(event) = self.receiver.try_recv() {
            self.diagnostics.push(event.to_string());
            if self.diagnostics.len() > 100 {
                self.diagnostics.remove(0);
            }
            if event["type"] == "progress" {
                self.status = event["message"].as_str().unwrap_or("Working…").into();
            } else if event["type"] == "result" {
                self.busy = false;
                if event["ok"] != true {
                    self.status = event["error"]
                        .as_str()
                        .unwrap_or("Operation failed.")
                        .into();
                    if ["shutdown_blocked", "elevation_declined"]
                        .contains(&event["code"].as_str().unwrap_or(""))
                    {
                        self.retry = Some(self.pending.clone());
                    } else {
                        self.preview = None;
                    }
                } else if self.pending == "preview" {
                    self.preview = Some(event["result"].clone());
                    self.status = if event["result"]["affected"]
                        .as_array()
                        .is_some_and(Vec::is_empty)
                    {
                        "Selection already applied."
                    } else {
                        "Selection verified. Ready to apply."
                    }
                    .into();
                } else if self.pending == "refresh" {
                    self.status = event["result"]["message"]
                        .as_str()
                        .unwrap_or("Compatibility refreshed.")
                        .into();
                    self.request("inspect", cx);
                } else if ["apply", "restore", "recover"].contains(&self.pending.as_str()) {
                    self.preview = None;
                    self.status = "Verified patch state installed.".into();
                    self.request("inspect", cx);
                } else {
                    self.state = event["result"].clone();
                    self.selected = self.state["current"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .collect();
                    self.status = if let Some(reason) = self.state["reason"].as_str() {
                        reason.into()
                    } else if self.state["native_only"] == true {
                        "Browser verified. Settings compatibility is unavailable.".into()
                    } else if self.state["installation"].is_null() {
                        "Choose an Aside installation to continue.".into()
                    } else {
                        "Your installation is ready.".into()
                    };
                }
            }
            cx.notify();
        }
    }

    fn toggle(
        &mut self,
        id: String,
        checked: bool,
        conflicts: Vec<String>,
        cx: &mut Context<Self>,
    ) {
        if checked {
            for conflict in conflicts {
                self.selected.remove(&conflict);
            }
            self.selected.insert(id);
        } else {
            self.selected.remove(&id);
        }
        self.preview = None;
        self.retry = None;
        self.status = "Review your selection before applying.".into();
        cx.notify();
    }
}

fn title(id: &str) -> &str {
    match id {
        "chats" => "Hide Chats",
        "chats-toggle" => "Chats visibility control",
        "ask-aside" => "Hide Ask Aside",
        "bookmark-folder-appearance" => "Bookmark folder appearance",
        _ => id,
    }
}

impl Render for Patcher {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let theme = cx.theme();
        let (background, foreground, muted, border, card) = (
            theme.background,
            theme.foreground,
            theme.muted_foreground,
            theme.border,
            theme.secondary,
        );
        let mut content = div()
            .flex()
            .flex_col()
            .gap_5()
            .p_6()
            .w_full()
            .child(
                div()
                    .flex()
                    .justify_between()
                    .items_center()
                    .child(
                        div()
                            .flex()
                            .flex_col()
                            .gap_1()
                            .child(div().text_2xl().font_semibold().child("Aside Patcher"))
                            .child(
                                div()
                                    .text_sm()
                                    .text_color(muted)
                                    .child("A few changes. Your browser, your way."),
                            ),
                    )
                    .child(
                        Button::new("refresh")
                            .label("Refresh compatibility")
                            .disabled(self.busy)
                            .on_click(cx.listener(|this, _, _, cx| this.request("refresh", cx))),
                    ),
            )
            .child(
                div()
                    .rounded_lg()
                    .border_1()
                    .border_color(border)
                    .p_4()
                    .flex()
                    .flex_col()
                    .gap_2()
                    .child(div().font_semibold().child(format!(
                        "Aside {}",
                        self.state["version"].as_str().unwrap_or("not selected")
                    )))
                    .child(
                        div().text_xs().text_color(muted).child(
                            self.state["installation"]["dll"]
                                .as_str()
                                .unwrap_or("No verified installation selected")
                                .to_string(),
                        ),
                    ),
            );
        if let Some(installations) = self.state["installations"].as_array()
            && self.installation_override.is_none()
            && (installations.len() > 1 || self.state["installation"].is_null())
        {
            for (index, installation) in installations.iter().enumerate() {
                let entry = installation.clone();
                content = content.child(
                    Button::new(SharedString::from(format!("installation-{index}")))
                        .label(SharedString::from(format!(
                            "{} · {}",
                            installation["version"].as_str().unwrap_or(""),
                            installation["dll"].as_str().unwrap_or("")
                        )))
                        .disabled(self.busy)
                        .on_click(cx.listener(move |this, _, _, cx| {
                            this.state["installation"] = entry.clone();
                            this.preview = None;
                            this.request("inspect", cx);
                        })),
                );
            }
        }
        if let Some(warning) = self.state["compatibility_warning"].as_str() {
            content = content.child(div().text_sm().text_color(muted).child(warning.to_string()));
        }
        content = content.child(div().text_sm().font_semibold().child("PATCHES"));
        if self.state["recovery_required"] == true {
            content = content.child(
                Button::new("recover")
                    .primary()
                    .label("Recover interrupted operation")
                    .disabled(self.busy)
                    .on_click(cx.listener(|this, _, _, cx| this.request("recover", cx))),
            );
        }
        if let Some(patches) = self.state["patches"].as_array() {
            for patch in patches {
                let id = patch["id"].as_str().unwrap_or("").to_string();
                let conflicts: Vec<String> = patch["conflicts"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect();
                let enabled = patch["editable"] == true;
                let toggle_id = id.clone();
                let mut row = div()
                    .flex()
                    .flex_col()
                    .gap_2()
                    .p_4()
                    .rounded_lg()
                    .border_1()
                    .border_color(border)
                    .child(
                        Checkbox::new(SharedString::from(id.clone()))
                            .label(SharedString::from(title(&id).to_string()))
                            .checked(self.selected.contains(&id))
                            .disabled(self.busy || !enabled)
                            .on_change(cx.listener(move |this, checked, _, cx| {
                                this.toggle(toggle_id.clone(), *checked, conflicts.clone(), cx)
                            })),
                    )
                    .child(
                        div()
                            .text_sm()
                            .text_color(muted)
                            .child(patch["description"].as_str().unwrap_or("").to_string()),
                    );
                if !enabled {
                    row = row.child(
                        div().text_xs().text_color(muted).child(
                            patch["reason"]
                                .as_str()
                                .unwrap_or("Unavailable for this version")
                                .to_string(),
                        ),
                    );
                    if let Some(replacement) = patch["official_replacement"]
                        .as_str()
                        .filter(|text| !text.is_empty())
                    {
                        row = row.child(
                            div()
                                .text_xs()
                                .text_color(muted)
                                .child(format!("Official replacement: {replacement}")),
                        );
                    }
                }
                content = content.child(row);
            }
        }
        let can_apply = self
            .preview
            .as_ref()
            .is_some_and(|p| p["affected"].as_array().is_some_and(|a| !a.is_empty()));
        if let Some(preview) = &self.preview {
            let desired = preview["desired"]
                .as_array()
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .map(title)
                        .collect::<Vec<_>>()
                        .join(", ")
                })
                .filter(|text| !text.is_empty())
                .unwrap_or_else(|| "Original appearance (no patches)".to_string());
            let summary = format!(
                "{} file(s) verified. {}",
                preview["affected"].as_array().map_or(0, Vec::len),
                if !can_apply {
                    "Selection already applied."
                } else if preview["requires_elevation"] == true {
                    "Windows will ask for administrator approval."
                } else {
                    "Ready to install."
                }
            );
            content = content.child(
                div()
                    .rounded_lg()
                    .bg(card)
                    .p_4()
                    .text_sm()
                    .flex()
                    .flex_col()
                    .gap_2()
                    .child(format!("After applying: {desired}"))
                    .child(div().text_color(muted).child(summary)),
            );
        }
        content = content
            .child(
                div()
                    .flex()
                    .gap_3()
                    .items_center()
                    .child(
                        Button::new("preview")
                            .label("Review selection")
                            .disabled(
                                self.busy
                                    || self.state["patches"].as_array().is_none_or(Vec::is_empty),
                            )
                            .on_click(cx.listener(|this, _, _, cx| this.request("preview", cx))),
                    )
                    .child(
                        Button::new("apply")
                            .primary()
                            .label("Apply patches")
                            .disabled(self.busy || !can_apply)
                            .on_click(cx.listener(|this, _, _, cx| this.request("apply", cx))),
                    )
                    .child(
                        Button::new("restore")
                            .ghost()
                            .label("Restore originals")
                            .disabled(self.busy || self.state["restore_supported"] != true)
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.selected.clear();
                                this.preview = None;
                                this.request("preview", cx);
                            })),
                    ),
            )
            .child(div().text_sm().text_color(muted).child(self.status.clone()))
            .child(
                Button::new("diagnostics")
                    .ghost()
                    .small()
                    .label(if self.show_diagnostics {
                        "Hide diagnostics"
                    } else {
                        "Show diagnostics"
                    })
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.show_diagnostics = !this.show_diagnostics;
                        cx.notify();
                    })),
            );
        if self.show_diagnostics {
            for line in self.diagnostics.iter().rev().take(10) {
                content = content.child(div().text_xs().text_color(muted).child(line.clone()));
            }
        }
        if self.retry.is_some() {
            content = content.child(
                div()
                    .flex()
                    .gap_3()
                    .child(
                        Button::new("retry")
                            .primary()
                            .label("Retry")
                            .disabled(self.busy)
                            .on_click(cx.listener(|this, _, _, cx| {
                                if let Some(operation) = this.retry.clone() {
                                    this.request(&operation, cx);
                                }
                            })),
                    )
                    .child(
                        Button::new("cancel")
                            .label("Cancel")
                            .disabled(self.busy)
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.retry = None;
                                this.preview = None;
                                this.status = "Canceled. No files changed.".into();
                                cx.notify();
                            })),
                    ),
            );
        }
        div()
            .id("patcher")
            .size_full()
            .overflow_y_scroll()
            .bg(background)
            .text_color(foreground)
            .child(content)
    }
}

fn main() {
    gpui_kit::application()
        .with_assets(gpui_kit::assets::Assets)
        .run(|cx| {
            gpui_kit::init(cx);
            let bounds = Bounds::centered(None, size(px(780.), px(860.)), cx);
            gpui_kit::open_window(
                WindowOptions {
                    window_bounds: Some(WindowBounds::Windowed(bounds)),
                    window_min_size: Some(size(px(620.), px(500.))),
                    ..Default::default()
                },
                cx,
                |window, cx| {
                    window.set_window_title("Aside Patcher");
                    Theme::change(window.appearance(), Some(window), cx);
                    window
                        .observe_window_appearance(|window, cx| {
                            Theme::change(window.appearance(), Some(window), cx);
                        })
                        .detach();
                    cx.new(Patcher::new)
                },
            )
            .expect("Could not open Aside Patcher");
            cx.activate(true);
        });
}
