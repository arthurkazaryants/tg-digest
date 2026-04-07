#!/usr/bin/env python3
"""
Session initialization script for Telegram authentication.
Creates tg_reader_session.txt or tg_publisher_session.txt through interactive Telegram login.
"""

import asyncio
import os
import sys
from pathlib import Path

from telethon import TelegramClient


def read_secret(name: str) -> str:
    """Read Docker secret from /run/secrets/<name>."""
    secret_path = Path(f"/run/secrets/{name}")
    if secret_path.exists():
        return secret_path.read_text().strip()
    raise FileNotFoundError(f"Secret {name} not found at {secret_path}")


async def init_session(session_type: str = "reader"):
    """
    Initialize Telegram session through interactive login.
    Session is saved to /tmp/ (writable location in container)
    
    Args:
        session_type: "reader" or "publisher"
    """
    try:
        api_id = int(read_secret("tg_api_id"))
        api_hash = read_secret("tg_api_hash")
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    
    session_name = f"tg_{session_type}_session.txt"
    session_file = Path(f"/tmp/{session_name}")  # Write to /tmp (writable)
    
    print(f"\n{'='*60}")
    print(f"Initializing Telegram session for {session_type.upper()}")
    print(f"Session will be saved to: {session_file}")
    print(f"{'='*60}\n")
    
    client = TelegramClient(str(session_file), api_id, api_hash)
    
    try:
        await client.start()
        print(f"\n✓ Session created successfully!")
        print(f"✓ Session file: {session_file}")
        print(f"\n📋 NEXT STEPS:")
        print(f"  1. Copy session file from container to host secrets:")
        print(f"     docker compose cp tg-digest-reader-run-XXXXXXXXX:/tmp/{session_name} ./tg-digest-secrets/{session_name}")
        print(f"  2. Or manually from container:")
        print(f"     docker compose exec reader cat /tmp/{session_name} > ./tg-digest-secrets/{session_name}")
        print(f"  3. Then start the {session_type} service:")
        print(f"     docker compose up -d {session_type}\n")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    session_type = sys.argv[1] if len(sys.argv) > 1 else "reader"
    
    if session_type not in ("reader", "publisher"):
        print(f"ERROR: Invalid session type '{session_type}'. Use 'reader' or 'publisher'")
        sys.exit(1)
    
    asyncio.run(init_session(session_type))
