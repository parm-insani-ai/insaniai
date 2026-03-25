#!/usr/bin/env python3
"""
Backup Script — Automated PostgreSQL database backups.

Usage:
    python scripts/backup.py                    # Run once
    python scripts/backup.py --schedule hourly  # Run on a cron-like schedule

Features:
- pg_dump-based full backups
- Gzip compression
- Automatic rotation (keeps last N backups)
- Optional upload to S3-compatible storage
- Logs to stdout for container environments

In production, run this as a cron job or Kubernetes CronJob:
    0 */6 * * * cd /app && python scripts/backup.py >> /var/log/backup.log 2>&1
"""

import os
import sys
import gzip
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# Configuration
BACKUP_DIR = os.getenv("BACKUP_DIR", "./backups")
DATABASE_URL = os.getenv("DATABASE_URL", "")
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", "48"))  # Keep last 48 (2 days at 1/hour)
S3_BUCKET = os.getenv("BACKUP_S3_BUCKET", "")       # Optional S3 upload


def log(msg: str):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{timestamp}] {msg}")


def parse_pg_url(url: str) -> dict:
    """Parse a PostgreSQL URL into connection components."""
    # postgresql+asyncpg://user:pass@host:port/dbname
    # or postgresql://user:pass@host:port/dbname
    url = url.replace("postgresql+asyncpg://", "").replace("postgresql://", "")
    userpass, hostdb = url.split("@", 1)
    user, password = userpass.split(":", 1)
    hostport, dbname = hostdb.split("/", 1)
    if ":" in hostport:
        host, port = hostport.split(":", 1)
    else:
        host, port = hostport, "5432"
    return {"user": user, "password": password, "host": host, "port": port, "dbname": dbname}


def backup_postgres():
    """Run pg_dump and compress the output."""
    if not DATABASE_URL or "postgresql" not in DATABASE_URL:
        log("ERROR: DATABASE_URL not set or not PostgreSQL. Skipping backup.")
        return None

    pg = parse_pg_url(DATABASE_URL)
    os.makedirs(BACKUP_DIR, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"insani_backup_{timestamp}.sql"
    filepath = Path(BACKUP_DIR) / filename
    gz_path = Path(BACKUP_DIR) / f"{filename}.gz"

    log(f"Starting backup of {pg['dbname']}@{pg['host']}...")

    env = os.environ.copy()
    env["PGPASSWORD"] = pg["password"]

    try:
        result = subprocess.run(
            [
                "pg_dump",
                "-h", pg["host"],
                "-p", pg["port"],
                "-U", pg["user"],
                "-d", pg["dbname"],
                "--format=plain",
                "--no-owner",
                "--no-privileges",
                "-f", str(filepath),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        if result.returncode != 0:
            log(f"ERROR: pg_dump failed: {result.stderr}")
            return None

        # Compress
        with open(filepath, 'rb') as f_in:
            with gzip.open(gz_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        filepath.unlink()  # Remove uncompressed

        size_mb = gz_path.stat().st_size / (1024 * 1024)
        log(f"Backup complete: {gz_path.name} ({size_mb:.1f} MB)")

        return gz_path

    except subprocess.TimeoutExpired:
        log("ERROR: pg_dump timed out after 5 minutes")
        return None
    except Exception as e:
        log(f"ERROR: Backup failed: {e}")
        return None


def rotate_backups():
    """Delete old backups, keeping only the most recent MAX_BACKUPS."""
    backup_dir = Path(BACKUP_DIR)
    if not backup_dir.exists():
        return

    backups = sorted(backup_dir.glob("insani_backup_*.sql.gz"), reverse=True)
    if len(backups) <= MAX_BACKUPS:
        return

    for old_backup in backups[MAX_BACKUPS:]:
        old_backup.unlink()
        log(f"Rotated old backup: {old_backup.name}")


def upload_to_s3(filepath: Path):
    """Upload backup to S3-compatible storage (optional)."""
    if not S3_BUCKET:
        return

    try:
        import boto3
        s3 = boto3.client("s3")
        key = f"backups/{filepath.name}"
        s3.upload_file(str(filepath), S3_BUCKET, key)
        log(f"Uploaded to s3://{S3_BUCKET}/{key}")
    except ImportError:
        log("WARN: boto3 not installed. Skipping S3 upload.")
    except Exception as e:
        log(f"ERROR: S3 upload failed: {e}")


def backup_sqlite():
    """Simple SQLite backup — just copy the file."""
    db_path = DATABASE_URL.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
    if not os.path.exists(db_path):
        log(f"SQLite file not found: {db_path}")
        return None

    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = Path(BACKUP_DIR) / f"insani_backup_{timestamp}.db.gz"

    with open(db_path, 'rb') as f_in:
        with gzip.open(backup_path, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)

    size_mb = backup_path.stat().st_size / (1024 * 1024)
    log(f"SQLite backup: {backup_path.name} ({size_mb:.1f} MB)")
    return backup_path


def main():
    log("=" * 50)
    log("insani database backup starting")

    if "postgresql" in DATABASE_URL:
        result = backup_postgres()
    elif "sqlite" in DATABASE_URL:
        result = backup_sqlite()
    else:
        log("ERROR: Unsupported DATABASE_URL format")
        sys.exit(1)

    if result:
        rotate_backups()
        upload_to_s3(result)
        log("Backup completed successfully")
    else:
        log("Backup FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
