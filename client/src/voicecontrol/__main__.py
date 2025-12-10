import importlib
import importlib.machinery
import pathlib
import sys

# Allow running as a script (e.g., PyInstaller entry) by fixing sys.path and
# loading main even if the package name is not importable.
if __package__ is None or __package__ == "":
    pkg_root = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(pkg_root.parent))
    try:
        main = importlib.import_module("voicecontrol.main").main
    except Exception:
        # Fallback: load main.py directly from disk/bundle location.
        main_path = pkg_root / "main.py"
        loader = importlib.machinery.SourceFileLoader("voicecontrol_main_shim", str(main_path))
        mod = loader.load_module()
        main = mod.main
else:
    from .main import main

if __name__ == "__main__":
    main()
