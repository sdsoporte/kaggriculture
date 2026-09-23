"""Combined monitor for active Kaggle submissions (V9.1 and V9)."""

import subprocess
import sys


def main():
    print("=" * 78)
    print("RUNNING V9.1 TELEMETRY MONITOR...")
    print("=" * 78)
    subprocess.run([sys.executable, "scripts/monitor_v9_1.py"])
    print("\n" + "=" * 78)
    print("RUNNING V9 TELEMETRY MONITOR...")
    print("=" * 78)
    subprocess.run([sys.executable, "scripts/monitor_v9.py"])


if __name__ == "__main__":
    main()

