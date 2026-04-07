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
from telethon.sessions import StringSession


def read_secret(name: str) -> str:
    """Read Docker secret from /run/secrets/<name>."""
    secret_path = Path(f"/run/secrets/{name}")
    if secret_path.exists():
        return secret_path.read_text().strip()
    raise FileNotFoundError(f"Secret {name} not found at {secret_path}")


async def init_session(session_type: str = "publisher"):
    """
    Initialize Telegram session through interactive login.
    Session content is output to stdout for piping to file.
    All status messages go to stderr.
    
    Args:
        session_type: "reader" or "publisher"
    """
    try:
        api_id = int(read_secret("tg_api_id"))
        api_hash = read_secret("tg_api_hash")
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    
    session_name = f"tg_{session_type}_session.txt"
    
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Initializing Telegram session for {session_type.upper()}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)
    
    # Use StringSession to get the session string
    client = TelegramClient(StringSession(), api_id, api_hash)
    
    try:
        await client.start()
        print(f"✓ Session created successfully!", file=sys.stderr)
        
        # Get session string and output to stdout
        session_string = client.session.save()
        print(session_string)
        
        print(f"\n✓ Session saved and piped to stdout", file=sys.stderr)
        print(f"✓ Use: docker compose run --rm {session_type} python init_session.py {session_type} > ./tg-digest-secrets/{session_name}\n", file=sys.stderr)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    session_type = sys.argv[1] if len(sys.argv) > 1 else "publisher"
    
    if session_type not in ("reader", "publisher"):
        print(f"ERROR: Invalid session type '{session_type}'. Use 'reader' or 'publisher'")
        sys.exit(1)
    
    asyncio.run(init_session(session_type))
