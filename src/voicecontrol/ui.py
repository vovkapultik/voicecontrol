import tkinter as tk
from tkinter import messagebox

from .auth import MasterPasswordProvider
from . import startup
from .devices import has_wasapi_output_devices


class AppUI:
    def __init__(
        self,
        controller,
        password_provider: MasterPasswordProvider,
    ) -> None:
        self.controller = controller
        self.config = controller.config
        self.recorder = controller.recorder
        self.password_provider = password_provider
        self._offline = False
        self._master_password = ""
        self.root = tk.Tk()
        self.root.withdraw()
        self.main_win: tk.Toplevel | None = None
        self.status_var = tk.StringVar(value="Stopped")
        self.device_status_var = tk.StringVar(value="")
        self.offline_var = tk.StringVar(value="")
        self.loopback_missing_var = tk.StringVar(value="")
        self._status_label: tk.Label | None = None
        self._toggle_btn: tk.Button | None = None
        self._device_status_label: tk.Label | None = None
        self._device_status_color = "red"

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

    def _build_main(self) -> None:
        self.root.title("VoiceControl Client")
        self.root.geometry("700x600")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        padding = {"padx": 12, "pady": 6}
        row = 0

        tk.Label(self.root, text="Recording").grid(row=row, column=0, sticky="w", **padding)
        self._status_label = tk.Label(self.root, textvariable=self.status_var, font=("Arial", 14, "bold"), fg="red")
        self._status_label.grid(row=row, column=1, sticky="w", **padding)
        row += 1
        self._toggle_btn = tk.Button(self.root, text="Start Recording", command=self._toggle_recording)
        self._toggle_btn.grid(row=row, column=0, **padding)
        row += 1

        tk.Label(self.root, text="Chunk length (seconds)").grid(row=row, column=0, sticky="w", **padding)
        chunk_var = tk.StringVar(value=str(self.config.config.chunk_seconds))
        chunk_entry = tk.Entry(self.root, textvariable=chunk_var, width=10)
        chunk_entry.grid(row=row, column=1, sticky="w", **padding)

        def save_chunk() -> None:
            try:
                val = int(chunk_var.get())
                if val < 5:
                    raise ValueError()
                self.config.update(chunk_seconds=val)
                messagebox.showinfo("Saved", f"Chunk length set to {val}s")
            except Exception:
                messagebox.showerror("Invalid", "Enter an integer >= 5")

        tk.Button(self.root, text="Save", command=save_chunk).grid(row=row, column=2, **padding)
        row += 1

        tk.Label(self.root, text="API key").grid(row=row, column=0, sticky="w", **padding)
        api_var = tk.StringVar(value=self.config.config.api_key)
        api_entry = tk.Entry(self.root, textvariable=api_var, width=30)
        api_entry.grid(row=row, column=1, columnspan=2, sticky="we", **padding)

        def save_api() -> None:
            self.config.update(api_key=api_var.get())
            messagebox.showinfo("Saved", "API key updated")

        tk.Button(self.root, text="Save", command=save_api).grid(row=row, column=3, **padding)
        row += 1

        tk.Label(self.root, text="Run on startup").grid(row=row, column=0, sticky="w", **padding)
        startup_var = tk.BooleanVar(value=self.config.config.run_on_startup)
        startup_chk = tk.Checkbutton(self.root, variable=startup_var, command=lambda: self._toggle_startup(startup_var))
        startup_chk.grid(row=row, column=1, sticky="w", **padding)
        if startup.is_enabled() and not startup_var.get():
            startup_var.set(True)
        row += 1

        tk.Label(self.root, text="Speaker device (loopback source)").grid(row=row, column=0, sticky="w", **padding)
        spk_devices, auto_choice, status = self.controller.auto_select_device()
        initial_val = f"{auto_choice[0]}:{auto_choice[1]}" if auto_choice else ""
        spk_var = tk.StringVar(value=initial_val)
        spk_options = [""] + [f"{idx}:{name}" for idx, name in spk_devices]
        spk_menu = tk.OptionMenu(self.root, spk_var, *spk_options)
        spk_menu.grid(row=row, column=1, columnspan=2, sticky="we", **padding)

        def save_spk() -> None:
            was_running = self.config.config.recording_enabled
            if was_running:
                self._stop_recording()
            sel = spk_var.get()
            if ":" in sel:
                try:
                    val = int(sel.split(":", 1)[0])
                    status = self.controller.set_device(val)
                    messagebox.showinfo("Saved", f"Speaker device set to {sel}")
                except Exception:
                    messagebox.showerror("Invalid", "Could not parse device selection.")
            else:
                status = self.controller.set_device(None)
                messagebox.showinfo("Saved", "Speaker device reset to default")
            self._set_device_status(status.text, status.color)
            if was_running:
                self._start_recording()

        tk.Button(self.root, text="Save", command=save_spk).grid(row=row, column=3, **padding)
        row += 1
        self._device_status_label = tk.Label(self.root, textvariable=self.device_status_var, fg=status.color)
        self._set_device_status(status.text, status.color)
        self._device_status_label.grid(row=row, column=0, columnspan=4, sticky="w", **padding)
        row += 1

        tk.Label(self.root, text="Recordings directory").grid(row=row, column=0, sticky="w", **padding)
        tk.Label(self.root, text=str(self.config.recordings_dir())).grid(row=row, column=1, columnspan=2, sticky="w", **padding)
        row += 1

        tk.Label(self.root, textvariable=self.offline_var, fg="red").grid(row=row, column=0, columnspan=3, sticky="w", **padding)
        if self._offline:
            self.offline_var.set("No internet access - using default password.")
        row += 1
        if not has_wasapi_output_devices():
            self.loopback_missing_var.set("No WASAPI loopback device found. Configure a Windows playback device before recording.")
            tk.Label(self.root, fg="red", textvariable=self.loopback_missing_var).grid(
                row=row, column=0, columnspan=4, sticky="w", **padding
            )
            row += 1

        tk.Button(self.root, text="Quit", command=self.root.quit).grid(row=row, column=0, **padding)

    def _on_close(self) -> None:
        try:
            self.recorder.stop()
        finally:
            self.root.quit()

    def _start_recording(self) -> None:
        ok, msg = self.controller.start_recording()
        color = "green" if ok else "red"
        self._set_status("Recording" if ok else msg, color)
        if self._toggle_btn:
            self._toggle_btn.config(text="Stop Recording" if ok else "Start Recording")
        if not ok:
            messagebox.showerror("Error", msg)

    def _stop_recording(self) -> None:
        ok, msg = self.controller.stop_recording()
        self._set_status("Stopped", "red")
        if self._toggle_btn:
            self._toggle_btn.config(text="Start Recording")
        if not ok:
            messagebox.showerror("Error", msg)

    def _toggle_startup(self, var: tk.BooleanVar) -> None:
        enabled = bool(var.get())
        self.config.update(run_on_startup=enabled)
        ok = startup.enable_startup() if enabled else startup.disable_startup()
        if not ok:
            messagebox.showerror("Startup", "Could not update startup registration on this system.")
            var.set(startup.is_enabled())

    def _toggle_recording(self) -> None:
        ok, msg, is_recording = self.controller.toggle_recording()
        if is_recording:
            self._set_status("Recording", "green")
            if self._toggle_btn:
                self._toggle_btn.config(text="Stop Recording")
        else:
            self._set_status("Stopped", "red" if ok else "red")
            if self._toggle_btn:
                self._toggle_btn.config(text="Start Recording")
        if not ok:
            messagebox.showerror("Error", msg)

    def _set_status(self, text: str, color: str) -> None:
        self.status_var.set(text)
        if self._status_label:
            self._status_label.config(fg=color)

    def _set_device_status(self, text: str, color: str) -> None:
        self.device_status_var.set(text)
        self._device_status_color = color
        if self._device_status_label:
            self._device_status_label.config(fg=color)
