"""Create private local configuration without overwriting existing credentials."""

import json
from pathlib import Path
import secrets

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = ROOT / ".env"
    if not env.exists():
        text = (ROOT / ".env.example").read_text()
        text = text.replace("replace-with-a-random-device-key", secrets.token_hex(24))
        text = text.replace("replace-with-a-different-random-dashboard-key", secrets.token_hex(24))
        with env.open("x") as f:
            f.write(text)
        env.chmod(0o600)
        print("Created .env with separate device and dashboard keys.")
    else:
        print("Kept existing .env.")
    config = ROOT / "firmware/include/config.h"
    if not config.exists():
        values = dotenv_values(env)
        key = values.get("THERMOSAFE_DEVICE_KEY", "")
        if not key or key.startswith("replace-"):
            raise SystemExit("Set the device key in .env before creating firmware configuration.")
        text = (ROOT / "firmware/include/config.example.h").read_text()
        text = text.replace('"replace-with-the-device-key-from-env"', json.dumps(key))
        with config.open("x") as f:
            f.write(text)
        config.chmod(0o600)
        print("Created firmware/include/config.h with the matching device key.")
    else:
        print("Kept existing firmware/include/config.h.")
    print("Set Wi-Fi, laptop API URL, and GPIO values in firmware/include/config.h.")


if __name__ == "__main__":
    main()
