from pathlib import Path
import random
import shutil
import subprocess

import pytest

from backend.risk import assess, heat_index_f


def test_known_noaa_equation_result_and_heat_index_only_classification():
    assert heat_index_f(90, 70) == pytest.approx(105.9220206)
    assert assess((90-32)/1.8, 70)["state"] == "critical"
    assert assess((110-32)/1.8, 10)["indicator_f"] == pytest.approx(104.4)
    assert assess((110-32)/1.8, 10)["state"] == "warning"
    assert assess(50, 50)["heat_index_c"] is None
    assert assess(50, 50)["heat_index_category"] == "unavailable"
    assert assess(None, None)["state"] == "unknown"


@pytest.mark.parametrize("temperature,humidity,state", [
    (23, 45, "normal"), (28, 50, "caution"), (32, 55, "warning"), (37, 65, "critical")
])
def test_demo_cases(temperature, humidity, state):
    assert assess(temperature, humidity)["state"] == state


def test_real_firmware_risk_header_matches_backend(tmp_path):
    compiler = shutil.which("clang++") or shutil.which("g++")
    if not compiler:
        pytest.skip("A C++ compiler is needed for firmware/backend parity")
    root = Path(__file__).resolve().parents[1]
    executable = tmp_path / "risk_probe"
    subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                    "-I", str(root / "firmware/include"), str(root / "tests/risk_probe.cpp"),
                    "-o", str(executable)], check=True, capture_output=True, text=True)
    rng = random.Random(42)
    cases = [(rng.uniform(0, 50), rng.uniform(0, 100)) for _ in range(400)]
    for boundary in (80, 90, 103, 112):
        for offset in (-.0001, 0, .0001):
            cases.extend([((boundary+offset-32)/1.8, humidity) for humidity in (5, 13, 50, 85, 90)])
    result = subprocess.run([str(executable)],
                            input="".join(f"{t:.17g} {rh:.17g}\n" for t, rh in cases),
                            capture_output=True, text=True, check=True)
    rows = result.stdout.splitlines()
    assert len(rows) == len(cases)
    for (temperature, humidity), row in zip(cases, rows):
        state, hi = row.split()
        assert state == assess(temperature, humidity)["state"]
        expected = heat_index_f(temperature*1.8+32, humidity)
        if expected is None:
            assert hi == "null"
        else:
            assert float(hi) == pytest.approx(expected, abs=1e-7)
