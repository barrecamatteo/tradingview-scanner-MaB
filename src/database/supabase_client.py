"""
Supabase database client for storing and retrieving Continuation Rate data.
"""

import os
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional

from supabase import create_client, Client

logger = logging.getLogger(__name__)


# ── SQL Schema (run this in Supabase SQL Editor) ──────────────────────────
SCHEMA_SQL = """
-- Current continuation rates (latest values)
CREATE TABLE IF NOT EXISTS continuation_rates (
    id BIGSERIAL PRIMARY KEY,
    asset VARCHAR(20) NOT NULL,
    category VARCHAR(50) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    cont_rate DECIMAL(5,2),
    confidence DECIMAL(3,2) DEFAULT 0,
    status VARCHAR(20) DEFAULT 'success',
    error_message TEXT,
    mode SMALLINT,         -- 1=Uptrend, -1=Downtrend
    status_code SMALLINT,  -- 0-8 indicator phase
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(asset, timeframe)
);

-- Historical data (all scans over time)
CREATE TABLE IF NOT EXISTS continuation_rates_history (
    id BIGSERIAL PRIMARY KEY,
    asset VARCHAR(20) NOT NULL,
    category VARCHAR(50) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    cont_rate DECIMAL(5,2),
    confidence DECIMAL(3,2) DEFAULT 0,
    mode SMALLINT,
    status_code SMALLINT,
    scan_batch_id UUID,
    scanned_at TIMESTAMPTZ DEFAULT NOW()
);

-- Scan log
CREATE TABLE IF NOT EXISTS scan_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    total_assets INT DEFAULT 0,
    successful INT DEFAULT 0,
    failed INT DEFAULT 0,
    status VARCHAR(20) DEFAULT 'running',
    error_message TEXT
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_rates_asset_tf ON continuation_rates(asset, timeframe);
CREATE INDEX IF NOT EXISTS idx_history_asset_tf ON continuation_rates_history(asset, timeframe);
CREATE INDEX IF NOT EXISTS idx_history_scanned_at ON continuation_rates_history(scanned_at);

-- Enable Row Level Security (optional, for production)
-- ALTER TABLE continuation_rates ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE continuation_rates_history ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE scan_log ENABLE ROW LEVEL SECURITY;
"""


class SupabaseDB:
    """Handles all database operations for the TradingView scanner."""

    def __init__(self, url: str = None, key: str = None):
        self.url = url or os.getenv("SUPABASE_URL")
        self.key = key or os.getenv("SUPABASE_KEY")

        if not self.url or not self.key:
            raise ValueError(
                "Supabase URL and Key required. Set SUPABASE_URL and SUPABASE_KEY env vars."
            )

        self.client: Client = create_client(self.url, self.key)
        logger.info("Supabase client initialized")

    # ── Scan Log ──────────────────────────────────────────────────────────

    def start_scan(self, total_assets: int) -> str:
        """Create a new scan log entry. Returns scan_batch_id."""
        result = (
            self.client.table("scan_log")
            .insert({
                "total_assets": total_assets,
                "status": "running",
            })
            .execute()
        )
        scan_id = result.data[0]["id"]
        logger.info(f"Scan started: {scan_id}")
        return scan_id

    def complete_scan(self, scan_id: str, successful: int, failed: int, error: str = None):
        """Mark a scan as completed."""
        status = "completed" if not error else "error"
        self.client.table("scan_log").update({
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "successful": successful,
            "failed": failed,
            "status": status,
            "error_message": error,
        }).eq("id", scan_id).execute()
        logger.info(f"Scan {scan_id} completed: {successful} ok, {failed} failed")

    # ── Current Rates ─────────────────────────────────────────────────────

    def upsert_rate(
        self,
        asset: str,
        category: str,
        timeframe: str,
        cont_rate: Optional[float],
        confidence: float = 0.0,
        status: str = "success",
        error_message: str = None,
        mode: Optional[int] = None,
        status_code: Optional[int] = None,
    ):
        """Insert or update a continuation rate."""
        data = {
            "asset": asset,
            "category": category,
            "timeframe": timeframe,
            "cont_rate": cont_rate,
            "confidence": confidence,
            "status": status,
            "error_message": error_message,
            "mode": mode,
            "status_code": status_code,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.client.table("continuation_rates").upsert(
            data, on_conflict="asset,timeframe"
        ).execute()

    def bulk_upsert_rates(self, records: List[Dict]):
        """Bulk upsert continuation rates."""
        if not records:
            return

        now = datetime.now(timezone.utc).isoformat()
        for r in records:
            r["updated_at"] = now

        self.client.table("continuation_rates").upsert(
            records, on_conflict="asset,timeframe"
        ).execute()
        logger.info(f"Bulk upserted {len(records)} rates")

    # ── History ───────────────────────────────────────────────────────────

    def add_history(
        self,
        asset: str,
        category: str,
        timeframe: str,
        cont_rate: Optional[float],
        confidence: float = 0.0,
        scan_batch_id: str = None,
        mode: Optional[int] = None,
        status_code: Optional[int] = None,
    ):
        """Add a historical record."""
        self.client.table("continuation_rates_history").insert({
            "asset": asset,
            "category": category,
            "timeframe": timeframe,
            "cont_rate": cont_rate,
            "confidence": confidence,
            "mode": mode,
            "status_code": status_code,
            "scan_batch_id": scan_batch_id,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

    def bulk_add_history(self, records: List[Dict]):
        """Bulk insert historical records."""
        if not records:
            return

        now = datetime.now(timezone.utc).isoformat()
        for r in records:
            r.setdefault("scanned_at", now)

        self.client.table("continuation_rates_history").insert(records).execute()
        logger.info(f"Bulk inserted {len(records)} history records")

    # ── Queries ───────────────────────────────────────────────────────────

    def get_all_rates(self) -> List[Dict]:
        """Get all current continuation rates."""
        result = (
            self.client.table("continuation_rates")
            .select("*")
            .order("category")
            .order("asset")
            .execute()
        )
        return result.data

    def get_rates_by_category(self, category: str) -> List[Dict]:
        """Get rates filtered by category."""
        result = (
            self.client.table("continuation_rates")
            .select("*")
            .eq("category", category)
            .order("asset")
            .execute()
        )
        return result.data

    def get_rates_pivot(self) -> List[Dict]:
        """
        Get rates in pivot format: one row per asset with columns for each timeframe.
        Returns list of dicts like:
        {asset, category, 4H, 1H, 15min, avg, updated_at}
        """
        all_rates = self.get_all_rates()

        # Group by asset
        asset_data = {}
        for row in all_rates:
            key = row["asset"]
            if key not in asset_data:
                asset_data[key] = {
                    "asset": row["asset"],
                    "category": row["category"],
                    "4H": None,
                    "1H": None,
                    "15min": None,
                    "updated_at": row["updated_at"],
                }
            asset_data[key][row["timeframe"]] = row["cont_rate"]
            # Keep latest timestamp
            if row["updated_at"] > asset_data[key]["updated_at"]:
                asset_data[key]["updated_at"] = row["updated_at"]

        # Calculate averages
        for asset in asset_data.values():
            values = [
                v for v in [asset["4H"], asset["1H"], asset["15min"]]
                if v is not None
            ]
            asset["avg"] = round(sum(values) / len(values), 1) if values else None

        return sorted(asset_data.values(), key=lambda x: (x["category"], x["asset"]))

    def get_history(
        self,
        asset: str = None,
        timeframe: str = None,
        limit: int = 100,
    ) -> List[Dict]:
        """Get historical data, optionally filtered by asset and timeframe."""
        query = self.client.table("continuation_rates_history").select("*")

        if asset:
            query = query.eq("asset", asset)
        if timeframe:
            query = query.eq("timeframe", timeframe)

        result = query.order("scanned_at", desc=True).limit(limit).execute()
        return result.data

    def get_last_scan(self) -> Optional[Dict]:
        """Get the most recent scan log entry."""
        result = (
            self.client.table("scan_log")
            .select("*")
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    def get_schema_sql(self) -> str:
        """Return the SQL schema for creating tables."""
        return SCHEMA_SQL
