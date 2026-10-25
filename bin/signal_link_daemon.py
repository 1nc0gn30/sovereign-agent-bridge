#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time

artifact_dir = os.environ.get("ARTIFACT_DIR", "/tmp/zoth-signal-artifacts")
os.makedirs(artifact_dir, exist_ok=True)
png_path = os.path.join(artifact_dir, "signal_link_qr.png")
info_path = "/tmp/signal_qr_info.json"
status_path = "/tmp/signal_link_status.json"

for p in [info_path, status_path]:
    if os.path.exists(p):
        try:
            os.remove(p)
        except OSError:
            pass

print("[*] Spawning signal-cli link -n 'Zoth-Studio-Bridge'...")
proc = subprocess.Popen(
    ["signal-cli", "link", "-n", "Zoth-Studio-Bridge"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1
)

uri = None
# Read line by line from stdout
while True:
    line = proc.stdout.readline()
    if not line:
        break
    line_str = line.strip()
    print(f"[signal-cli] {line_str}")
    if "sgnl://linkdevice" in line_str or "tsdevice:/" in line_str:
        uri = line_str
        break

if not uri:
    err = proc.stderr.read()
    print(f"[!] Failed to get tsdevice URI. Stderr: {err}")
    with open(status_path, "w") as f:
        json.dump({"status": "error", "error": err}, f)
    sys.exit(1)

# Generate QR PNG
subprocess.run(["qrencode", "-o", png_path, "-s", "8", "-m", "2", uri], check=True)

# Generate UTF8 string for terminal display
utf8_qr = subprocess.check_output(["qrencode", "-t", "UTF8", "-m", "2", uri], text=True)

info = {
    "uri": uri,
    "png_path": png_path,
    "utf8_qr": utf8_qr,
    "timestamp": time.time()
}
with open(info_path, "w") as f:
    json.dump(info, f, indent=2)

print("\n" + "=" * 60)
print("SIGNAL LINK QR CODE GENERATED:")
print("=" * 60 + "\n")
print(utf8_qr)
print("\nURI: " + uri)
print(f"PNG saved to: {png_path}")
print("=" * 60)
print("[*] Waiting for scan on your phone in Signal (Settings > Linked Devices)...")
sys.stdout.flush()

# Now wait for process to finish
try:
    stdout_rem, stderr_rem = proc.communicate(timeout=180)
    print(f"[stdout] {stdout_rem}")
    if stderr_rem:
        print(f"[stderr] {stderr_rem}")
    if proc.returncode == 0:
        print("[✔] Successfully linked Signal device!")
        # Query listAccounts
        acc_out = subprocess.check_output(["signal-cli", "listAccounts"], text=True)
        print(f"Accounts: {acc_out}")
        with open(status_path, "w") as f:
            json.dump({"status": "success", "accounts": acc_out.strip(), "returncode": 0}, f)
    else:
        print(f"[!] Linking failed with returncode {proc.returncode}")
        with open(status_path, "w") as f:
            json.dump({"status": "failed", "returncode": proc.returncode, "error": stderr_rem}, f)
except subprocess.TimeoutExpired:
    proc.kill()
    print("[!] Linking timed out after 180 seconds.")
    with open(status_path, "w") as f:
        json.dump({"status": "timeout"}, f)
