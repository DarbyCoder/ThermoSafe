"""Capture a short raw pulse waveform from the ESP32 for troubleshooting."""

import argparse
import csv
import tempfile
import time
from pathlib import Path

import serial


def sparkline(values):
    blocks = "▁▂▃▄▅▆▇█"
    low, high = min(values), max(values)
    if high == low:
        return blocks[0] * len(values)
    return "".join(blocks[round((value - low) * 7 / (high - low))] for value in values)


def main():
    parser = argparse.ArgumentParser(description="Capture ESP32 pulse ADC samples")
    parser.add_argument("--port", default="/dev/cu.usbserial-0001")
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args()

    samples = []
    with serial.Serial(args.port, 115200, timeout=0.25) as device:
        time.sleep(2.0)
        device.reset_input_buffer()
        device.write(b"p")
        end = time.monotonic() + args.seconds
        while time.monotonic() < end:
            line = device.readline().decode("ascii", errors="ignore").strip()
            if not line.startswith("WAVE,"):
                continue
            _, timestamp, raw = line.split(",", 2)
            samples.append((int(timestamp), int(raw)))
        device.write(b"x")

    output = Path(tempfile.gettempdir()) / "thermosafe_pulse_capture.csv"
    with output.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("millis", "adc"))
        writer.writerows(samples)

    print(f"Captured {len(samples)} samples at about 100 Hz.")
    if not samples:
        print("No waveform samples arrived. Check that the updated firmware is running.")
        return
    raw_values = [raw for _, raw in samples]
    print(f"Overall ADC range: {min(raw_values)}–{max(raw_values)} (swing {max(raw_values)-min(raw_values)}).")
    print("Each row below is about one second; the sparkline is centered within that second.")
    for start in range(0, len(samples), 100):
        chunk = samples[start : start + 100]
        values = [raw for _, raw in chunk]
        average = sum(values) / len(values)
        # Compress 100 samples to 20 points while preserving the waveform shape.
        points = [sum(values[i : i + 5]) / len(values[i : i + 5]) for i in range(0, len(values), 5)]
        centered = [point - average for point in points]
        print(f"{start/100:>4.0f}s  raw {min(values):4d}–{max(values):4d}  swing {max(values)-min(values):4d}  {sparkline(centered)}")
    print(f"Temporary capture: {output}")


if __name__ == "__main__":
    main()
