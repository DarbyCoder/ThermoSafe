"""Send LED demo controls through the USB-connected Site Manager graph reader."""

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def send(base_url: str, mode: str, target: str):
    request = Request(
        base_url.rstrip("/") + "/control",
        data=json.dumps({"mode": mode, "target": target}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Control worker LEDs and buzzer through the Site Manager")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="Mesh graph/control server URL")
    parser.add_argument("--target", default="all", help="Worker mesh node ID, device ID, or all")
    args = parser.parse_args()

    target = args.target
    print("HeatSentinel mesh demo controls")
    print(f"Target: {target}  |  Commands: g=green, y=yellow + one beep, r=red + repeating buzzer, a=auto")
    print("Change target with: target <mesh-node-id|device-id|all>. Quit with q.")
    while True:
        try:
            command = input(f"[{target}] > ").strip().lower().split()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not command:
            continue
        if command[0] in {"q", "quit", "exit"}:
            break
        if command[0] == "target":
            if len(command) != 2:
                print("Usage: target <mesh-node-id|device-id|all>")
            else:
                target = command[1]
                print(f"Target set to {target}")
            continue
        aliases = {"g": "green", "green": "green", "y": "yellow", "yellow": "yellow",
                   "r": "red", "red": "red", "a": "auto", "auto": "auto"}
        mode = aliases.get(command[0])
        if mode is None:
            print("Commands: g, y, r, a, target <id|all>, q")
            continue
        if len(command) > 2:
            print("Usage: g|y|r|a [worker-id|all]")
            continue
        selected_target = command[1] if len(command) == 2 else target
        try:
            result = send(args.url, mode, selected_target)
            print(f"Queued {mode} for {selected_target} through the Site Manager")
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            print(f"Could not send command: {error}")


if __name__ == "__main__":
    main()
