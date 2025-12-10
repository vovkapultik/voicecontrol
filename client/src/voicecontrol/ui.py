import tkinter as tk
from tkinter import messagebox, ttk

from .auth import MasterPasswordProvider
from .devices import has_wasapi_output_devices


class AppUI:
    def __init__(
        self,
        controller,
        password_provider: MasterPasswordProvider,
        uploader=None,
    ) -> None:
        self.controller = controller
        self.config = controller.config
        self.recorder = controller.recorder
        self.password_provider = password_provider
        self.uploader = uploader
        self._offline = False
        self._master_password = ""
        self.root = tk.Tk()
        self.root.withdraw()
        self.main_win: tk.Toplevel | None = None
        self.status_var = tk.StringVar(value="Stopped")
        self.device_status_var = tk.StringVar(value="")
        self.mic_status_var = tk.StringVar(value="")
        self.offline_var = tk.StringVar(value="")
        self.loopback_missing_var = tk.StringVar(value="")
        self.api_key_var = tk.StringVar(value=self.config.config.api_key or "")
        self.api_key_display_var = tk.StringVar(value=self._mask_key(self.api_key_var.get()))
        self.server_var = tk.StringVar(value=self.config.config.server_base)
        self.chunk_text_var = tk.StringVar(value="Chunk length: 1s (fixed)")
        self._status_badge: tk.Label | None = None
        self._toggle_btn: ttk.Button | None = None
        self._device_status_label: tk.Label | None = None
        self._mic_status_label: tk.Label | None = None
        self._colors = {
            "bg": "#0f172a",
            "surface": "#111827",
            "fg": "#e5e7eb",
            "muted": "#94a3b8",
            "primary": "#06b6d4",
            "primary_active": "#0891b2",
            "success": "#22c55e",
            "danger": "#ef4444",
        }

    def run(self) -> None:
        # Password fetch and login temporarily disabled.
        # self._master_password, self._offline = self.password_provider.fetch()
        # self._show_login()
        # Always start in a stopped state.
        if self.config.config.recording_enabled:
            self.config.update(recording_enabled=False)
        self.root.deiconify()
        self._build_main()
        self.root.mainloop()

    def _show_login(self) -> None:
        # Login UI disabled for now.
        pass

    def _build_style(self) -> None:
        colors = self._colors
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Base.TFrame", background=colors["bg"])
        style.configure("Card.TFrame", background=colors["surface"])
        style.configure("Title.TLabel", background=colors["bg"], foreground=colors["fg"], font=("Segoe UI", 16, "bold"))
        style.configure("Section.TLabel", background=colors["bg"], foreground=colors["fg"], font=("Segoe UI", 12, "bold"))
        style.configure("Label.TLabel", background=colors["surface"], foreground=colors["fg"], font=("Segoe UI", 10, "bold"))
        style.configure("Value.TLabel", background=colors["surface"], foreground=colors["fg"], font=("Segoe UI", 10))
        style.configure("Subtle.TLabel", background=colors["bg"], foreground=colors["muted"], font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=colors["surface"], foreground=colors["muted"], font=("Segoe UI", 9))
        style.configure("Primary.TButton", background=colors["primary"], foreground=colors["bg"], font=("Segoe UI", 11, "bold"), padding=8)
        style.configure("Secondary.TButton", background=colors["surface"], foreground=colors["fg"], font=("Segoe UI", 10, "bold"))
        style.map(
            "Primary.TButton",
            background=[("active", colors["primary_active"])],
            foreground=[("active", colors["bg"])],
        )
        style.map(
            "Secondary.TButton",
            background=[("active", colors["bg"])],
            foreground=[("active", colors["fg"])],
        )

    def _build_main(self) -> None:
        self.root.title("VoiceControl Client")
        self.root.geometry("560x620")
        self.root.resizable(False, False)
        self.root.configure(bg=self._colors["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_style()

        padding_y = (0, 14)
        container = ttk.Frame(self.root, style="Base.TFrame")
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, background=self._colors["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        main = ttk.Frame(canvas, padding=18, style="Base.TFrame")
        canvas_window = canvas.create_window((0, 0), window=main, anchor="nw")

        def _configure_scroll(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(canvas_window, width=canvas.winfo_width())

        main.bind("<Configure>", _configure_scroll)
        canvas.bind("<Configure>", _configure_scroll)

        header = ttk.Frame(main, style="Base.TFrame")
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="VoiceControl", style="Title.TLabel").pack(anchor="w")

        status_card = ttk.Frame(main, style="Card.TFrame", padding=14)
        status_card.pack(fill="x", pady=padding_y)
        status_row = ttk.Frame(status_card, style="Card.TFrame")
        status_row.grid(row=0, column=0, sticky="we")
        self._status_badge = tk.Label(
            status_row,
            textvariable=self.status_var,
            bg=self._colors["danger"],
            fg=self._colors["bg"],
            font=("Segoe UI", 12, "bold"),
            padx=12,
            pady=6,
        )
        self._status_badge.pack(side="left")
        ttk.Label(status_row, textvariable=self.chunk_text_var, style="Value.TLabel").pack(side="left", padx=12)
        ttk.Label(status_row, text="1s slices sent as they are ready", style="Muted.TLabel").pack(side="left", padx=4)

        self._toggle_btn = ttk.Button(status_card, text="Start Recording", command=self._toggle_recording, style="Primary.TButton")
        self._toggle_btn.grid(row=1, column=0, sticky="we", pady=(12, 0))

        devices_section = ttk.Frame(main, style="Base.TFrame")
        devices_section.pack(fill="x", pady=padding_y)
        ttk.Label(devices_section, text="Audio paths", style="Section.TLabel").pack(anchor="w", pady=(0, 6))

        spk_devices, auto_choice, status = self.controller.auto_select_device()
        self._build_device_card(
            devices_section,
            title="Speakers (loopback)",
            devices=spk_devices,
            auto_choice=auto_choice,
            status=status,
            save_callback=self._save_speaker_selection,
            status_var=self.device_status_var,
            label_attr="_device_status_label",
        )

        mic_devices, mic_choice, mic_status = self.controller.auto_select_mic()
        self._build_device_card(
            devices_section,
            title="Microphone",
            devices=mic_devices,
            auto_choice=mic_choice,
            status=mic_status,
            save_callback=self._save_mic_selection,
            status_var=self.mic_status_var,
            label_attr="_mic_status_label",
        )

        connection_card = ttk.Frame(main, style="Card.TFrame", padding=14)
        connection_card.pack(fill="x", pady=padding_y)
        connection_card.columnconfigure(1, weight=1)
        ttk.Label(connection_card, text="Server URL", style="Label.TLabel").grid(row=0, column=0, sticky="w")
        server_entry = ttk.Entry(connection_card, textvariable=self.server_var, width=42)
        server_entry.grid(row=0, column=1, sticky="we")
        ttk.Button(connection_card, text="Save URL", command=self._save_server_base, style="Secondary.TButton").grid(
            row=0, column=2, sticky="e", padx=(10, 0)
        )
        ttk.Label(connection_card, text="API key", style="Label.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 0))
        ttk.Label(connection_card, textvariable=self.api_key_display_var, style="Value.TLabel").grid(
            row=1, column=1, sticky="w", pady=(12, 0)
        )
        ttk.Label(connection_card, text="Enter or update your key", style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 2))
        api_entry = ttk.Entry(connection_card, textvariable=self.api_key_var, width=38)
        api_entry.grid(row=3, column=0, columnspan=2, sticky="we")
        ttk.Button(connection_card, text="Save API Key", command=self._save_api_key, style="Primary.TButton").grid(row=4, column=0, columnspan=2, sticky="we", pady=(10, 0))

        if self._offline:
            self.offline_var.set("No internet access - using default password.")
            tk.Label(connection_card, textvariable=self.offline_var, fg=self._colors["danger"], bg=self._colors["surface"]).grid(
                row=5, column=0, columnspan=2, sticky="w", pady=(10, 0)
            )

        if not has_wasapi_output_devices():
            self.loopback_missing_var.set("No WASAPI loopback device found. Configure a Windows playback device before recording.")
            tk.Label(main, fg=self._colors["danger"], bg=self._colors["bg"], textvariable=self.loopback_missing_var, wraplength=480, justify="left").pack(
                fill="x", pady=(0, 8)
            )

        footer = ttk.Frame(main, style="Base.TFrame")
        footer.pack(fill="x", pady=(6, 0))
        ttk.Button(footer, text="Quit", command=self.root.quit, style="Secondary.TButton").pack(side="right")

        # Set initial status colors.
        self._set_status("Stopped", self._colors["danger"])
        self._set_device_status(status.text, status.color)
        self._set_mic_status(mic_status.text, mic_status.color)

    def _build_device_card(
        self,
        parent: ttk.Frame,
        title: str,
        devices: list[tuple[int, str]],
        auto_choice: tuple[int, str] | None,
        status,
        save_callback,
        status_var: tk.StringVar,
        label_attr: str,
    ) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=12)
        card.pack(fill="x", pady=(0, 10))
        card.columnconfigure(1, weight=1)

        header = ttk.Frame(card, style="Card.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 6))
        ttk.Label(header, text=title, style="Label.TLabel").pack(side="left", anchor="w")

        ttk.Label(card, text="Pick manually or leave blank to stay with auto/default", style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w"
        )

        selection_var = tk.StringVar(value=self._format_selection(auto_choice))
        options = self._options_from_devices(devices)
        combo_state = "readonly" if options else "disabled"
        combo = ttk.Combobox(card, textvariable=selection_var, values=options or ["No devices found"], state=combo_state)
        combo.grid(row=2, column=0, sticky="we", pady=(8, 0))
        save_state = tk.NORMAL if options else tk.DISABLED
        ttk.Button(card, text="Save", command=lambda: save_callback(selection_var.get()), style="Secondary.TButton", state=save_state).grid(
            row=2, column=1, sticky="e", padx=(10, 0), pady=(8, 0)
        )

        label = tk.Label(
            card,
            textvariable=status_var,
            fg=status.color,
            bg=self._colors["surface"],
            anchor="w",
            justify="left",
            wraplength=420,
        )
        label.grid(row=3, column=0, columnspan=2, sticky="we", pady=(10, 0))
        setattr(self, label_attr, label)

    def _save_api_key(self) -> None:
        key = self.api_key_var.get().strip()
        self.config.update(api_key=key)
        self.api_key_display_var.set(self._mask_key(key))
        messagebox.showinfo("Saved", "API key updated.")

    def _save_server_base(self) -> None:
        value = self.server_var.get().strip()
        if not value:
            messagebox.showerror("Invalid URL", "Server URL cannot be empty.")
            return
        self.config.update(server_base=value)
        self.server_var.set(value)
        if self.uploader:
            try:
                self.uploader.set_server_base(value)
            except Exception:
                messagebox.showerror("Server URL", "Could not apply server URL to uploader.")
        messagebox.showinfo("Saved", f"Server URL set to {value}")

    def _save_speaker_selection(self, selection: str) -> None:
        was_running = self.config.config.recording_enabled
        if was_running:
            self._stop_recording()
        status = self.controller.set_device(self._parse_selection(selection))
        self._set_device_status(status.text, status.color)
        if was_running:
            self._start_recording()

    def _save_mic_selection(self, selection: str) -> None:
        was_running = self.config.config.recording_enabled
        if was_running:
            self._stop_recording()
        mic_status = self.controller.set_mic(self._parse_selection(selection))
        self._set_mic_status(mic_status.text, mic_status.color)
        if was_running:
            self._start_recording()

    def _parse_selection(self, raw: str) -> int | None:
        if ":" not in raw:
            return None
        try:
            return int(raw.split(":", 1)[0])
        except Exception:
            messagebox.showerror("Invalid selection", "Could not parse device selection.")
            return None

    def _format_selection(self, choice: tuple[int, str] | None) -> str:
        if not choice:
            return ""
        return f"{choice[0]}: {choice[1]}"

    def _options_from_devices(self, devices: list[tuple[int, str]]) -> list[str]:
        return [f"{idx}: {name}" for idx, name in devices]

    def _mask_key(self, key: str) -> str:
        key = (key or "").strip()
        if not key:
            return "Not set"
        if len(key) <= 4:
            return "*" * len(key)
        return f"{key[:2]}***{key[-2:]}"

    def _on_close(self) -> None:
        try:
            self.recorder.stop()
        finally:
            self.root.quit()

    def _start_recording(self) -> None:
        ok, msg = self.controller.start_recording()
        color = self._colors["success"] if ok else self._colors["danger"]
        self._set_status("Recording" if ok else msg, color)
        if self._toggle_btn:
            self._toggle_btn.config(text="Stop Recording" if ok else "Start Recording")
        if not ok:
            messagebox.showerror("Error", msg)

    def _stop_recording(self) -> None:
        ok, msg = self.controller.stop_recording()
        self._set_status("Stopped", self._colors["danger"])
        if self._toggle_btn:
            self._toggle_btn.config(text="Start Recording")
        if not ok:
            messagebox.showerror("Error", msg)

    def _toggle_recording(self) -> None:
        ok, msg, is_recording = self.controller.toggle_recording()
        if is_recording:
            self._set_status("Recording", self._colors["success"])
            if self._toggle_btn:
                self._toggle_btn.config(text="Stop Recording")
        else:
            self._set_status("Stopped", self._colors["danger"])
            if self._toggle_btn:
                self._toggle_btn.config(text="Start Recording")
        if not ok:
            messagebox.showerror("Error", msg)

    def _set_status(self, text: str, color: str) -> None:
        self.status_var.set(text)
        if self._status_badge:
            self._status_badge.config(bg=color)

    def _set_device_status(self, text: str, color: str) -> None:
        self.device_status_var.set(text)
        if self._device_status_label:
            self._device_status_label.config(fg=color)

    def _set_mic_status(self, text: str, color: str) -> None:
        self.mic_status_var.set(text)
        if self._mic_status_label:
            self._mic_status_label.config(fg=color)
