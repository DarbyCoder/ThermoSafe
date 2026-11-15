"""Package source and integration files, explicitly excluding local credentials/data."""

from pathlib import Path
import zipfile

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main():
    files = [ROOT / name for name in (
        "README.md", ".gitignore", ".env.example", "requirements.txt",
        "requirements-dev.txt", "requirements-dev.lock.txt", "firmware/platformio.ini",
        "firmware/include/config.example.h", "firmware/include/heat_risk.h",
        "firmware/include/pulse_estimator.h",
    )]
    for directory, extensions in (
        ("backend", {".py"}), ("scripts", {".py"}), ("tests", {".py", ".cpp"}),
        ("firmware/src", {".cpp"}), ("teammate", {".ts"}), ("docs", {".md", ".json"}),
    ):
        files.extend(p for p in (ROOT / directory).rglob("*") if p.is_file() and p.suffix in extensions)
    target = ROOT / "dist/ThermoSafe-device-backend.zip"
    target.parent.mkdir(exist_ok=True)
    # Inspect source bytes rather than serializing secret values into any output.
    private_values = dotenv_values(ROOT / ".env")
    keys = [private_values.get(name) for name in ("THERMOSAFE_DEVICE_KEY", "THERMOSAFE_DASHBOARD_KEY")]
    for path in files:
        content = path.read_bytes()
        if any(key and key.encode() in content for key in keys):
            raise SystemExit(f"Refusing to package a local API key in {path.relative_to(ROOT)}")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            archive.write(path, "ThermoSafe-device-backend/"+path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert not any(name.endswith("/config.h") or name.endswith("/.env") for name in archive.namelist())
    print(f"Packaged {len(set(files))} files; {target.stat().st_size:,} bytes; credentials excluded.")
    print(target)


if __name__ == "__main__":
    main()
