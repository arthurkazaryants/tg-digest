#!/usr/bin/env python3
"""
Health check script for publisher service.
Checks if process is running and can connect to database.
"""

import asyncio
import sys
from pathlib import Path

import asyncpg


async def check_health():
    """Check if service is healthy."""
    try:
        # Read password from secret
        password = Path("/run/secrets/pg_password").read_text().strip()
        
        # Try to connect to database
        conn = await asyncpg.connect(
            host="postgres",
            port=5432,
            user="tg_digest",
            password=password,
            database="tg_digest",
            timeout=5
        )
        await conn.close()
        
        print("✓ Health check passed")
        return 0
    except Exception as e:
        print(f"✗ Health check failed: {e}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(check_health())
    sys.exit(exit_code)
