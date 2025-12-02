import logging
import sys
import time
import tkinter as tk
from tkinter import messagebox

from .audio_recorder import AudioRecorder
from .auth import MasterPasswordProvider
from .config import ConfigManager
from . import startup
from .devices import list_input_devices, list_output_devices, default_input_device, has_wasapi_output_devices


class AppUI:
    def __init__(
        self,
        config: ConfigManager,
        recorder: AudioRecorder,
        password_provider: MasterPasswordProvider,
    ) -> None:
        self.config = config
        self.recorder = recorder
        self.password_provider = password_provider
        self._offline = False
        self._master_password = ""
        self.root = tk.Tk()
        self.root.withdraw()
        self.main_win: tk.Toplevel | None = None
        self.status_var = tk.StringVar(value="Stopped")
        self.offline_var = tk.StringVar(value="")
        self.loopback_missing_var = tk.StringVar(value="")

    def run(self) -> None:
        # Password fetch and login temporarily disabled.
        # self._master_password, self._offline = self.password_provider.fetch()
        # self._show_login()
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
        tk.Label(self.root, textvariable=self.status_var).grid(row=row, column=1, sticky="w", **padding)
        row += 1
        tk.Button(self.root, text="Start", command=self._start_recording).grid(row=row, column=0, **padding)
        tk.Button(self.root, text="Stop", command=self._stop_recording).grid(row=row, column=1, **padding)
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

        tk.Label(self.root, text="Mic device").grid(row=row, column=0, sticky="w", **padding)
        devices = list_input_devices()
        mic_var = tk.StringVar(value=str(self.config.config.mic_device) if self.config.config.mic_device is not None else "")
        options = [f"{idx}:{name}" for idx, name in devices]
        if not options:
            options = ["No input devices found"]
        mic_menu = tk.OptionMenu(self.root, mic_var, *options)
        mic_menu.grid(row=row, column=1, columnspan=2, sticky="we", **padding)

        def save_mic() -> None:
            sel = mic_var.get()
            if ":" in sel:
                try:
                    val = int(sel.split(":", 1)[0])
                    self.config.update(mic_device=val)
                    self.recorder.mic_device = val
                    messagebox.showinfo("Saved", f"Mic device set to {sel}")
                except Exception:
                    messagebox.showerror("Invalid", "Could not parse device selection.")

        tk.Button(self.root, text="Save", command=save_mic).grid(row=row, column=3, **padding)
        row += 1

        tk.Label(self.root, text="Recordings directory").grid(row=row, column=0, sticky="w", **padding)
        tk.Label(self.root, text=str(self.config.recordings_dir())).grid(row=row, column=1, columnspan=2, sticky="w", **padding)
        row += 1

        tk.Label(self.root, textvariable=self.offline_var, fg="red").grid(row=row, column=0, columnspan=3, sticky="w", **padding)
        if self._offline:
            self.offline_var.set("No internet access - using default password.")
        row += 1
        if sys.platform.startswith("darwin") or sys.platform.startswith("linux"):
            tk.Label(self.root, fg="red", text="Speaker loopback capture requires Windows/WASAPI.").grid(
                row=row, column=0, columnspan=3, sticky="w", **padding
            )
            row += 1

        if sys.platform.startswith("win") and not has_wasapi_output_devices():
            self.loopback_missing_var.set("No WASAPI loopback device found. Install VB-CABLE?")
            tk.Label(self.root, fg="red", textvariable=self.loopback_missing_var).grid(
                row=row, column=0, columnspan=4, sticky="w", **padding
            )
            row += 1

            def install_driver() -> None:
                ok = install_vb_cable()
                if ok:
                    messagebox.showinfo("Loopback", "Installer launched. Complete it, then restart the app.")
                    self.loopback_missing_var.set("Installer launched; restart after completion.")
                else:
                    messagebox.showerror("Loopback", "Failed to launch VB-CABLE installer. Ensure the installer file exists.")

            tk.Button(self.root, text="Install loopback driver", command=install_driver).grid(row=row, column=0, **padding)
            row += 1

        tk.Button(self.root, text="Quit", command=self.root.quit).grid(row=row, column=0, **padding)

        if self.config.config.recording_enabled:
            self._start_recording()

    def _on_close(self) -> None:
        try:
            self.recorder.stop()
        finally:
            self.root.quit()

    def _start_recording(self) -> None:
        self.config.update(recording_enabled=True)
        try:
            self.recorder.chunk_seconds = self.config.config.chunk_seconds
            self.recorder.start()
            self.status_var.set("Recording")
        except Exception as exc:
            logging.exception("Failed to start recording: %s", exc)
            messagebox.showerror("Error", f"Could not start recording: {exc}")

    def _stop_recording(self) -> None:
        self.config.update(recording_enabled=False)
        try:
            self.recorder.stop()
            self.status_var.set("Stopped")
        except Exception as exc:
            logging.exception("Failed to stop recording: %s", exc)
            messagebox.showerror("Error", f"Could not stop recording: {exc}")

    def _toggle_startup(self, var: tk.BooleanVar) -> None:
        enabled = bool(var.get())
        self.config.update(run_on_startup=enabled)
        ok = startup.enable_startup() if enabled else startup.disable_startup()
        if not ok:
            messagebox.showerror("Startup", "Could not update startup registration on this system.")
            var.set(startup.is_enabled())
