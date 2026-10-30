#!/usr/bin/env python3
import sys, os
from pathlib import Path

# Add src to sys.path
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import argparse
from sovereign_agent_bridge.ui_server import BridgeUIServer

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sovereign Agent Bridge Daemon")
    parser.add_argument("--port", type=int, default=8102)
    parser.add_argument("--host", default="127.0.0.1")
    args, _ = parser.parse_known_args()
    
    server = BridgeUIServer(host=args.host, port=args.port)
    print(f"Launching Sovereign Agent Bridge on http://{args.host}:{args.port} ...")
    server.start(blocking=True)
