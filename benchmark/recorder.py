"""Episode outcome and trajectory recorder for InfraBench.

Serializes benchmark episodes, agent tool trajectories, fault results, and independent
evaluation reports to structured JSON files for reproducibility and analysis.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from benchmark.episode import Episode

logger = logging.getLogger("infrabench.recorder")


class EpisodeRecorder:
    """Persists episode trajectories and evaluation reports to disk."""

    def __init__(self, records_dir: Union[str, Path] = "benchmark/records") -> None:
        self.records_dir = Path(records_dir)
        self.records_dir.mkdir(parents=True, exist_ok=True)

    def _to_iso(self, val: Optional[Union[datetime.datetime, str]]) -> Optional[str]:
        """Safely convert datetime or str to ISO 8601 string."""
        if val is None:
            return None
        if isinstance(val, datetime.datetime):
            return val.isoformat()
        return str(val)

    def serialize_episode(self, episode: Episode) -> Dict[str, Any]:
        """Convert Episode model into a clean, JSON-serializable dictionary."""
        created_at_iso = self._to_iso(episode.created_at)
        started_at_iso = self._to_iso(episode.started_at)
        finished_at_iso = self._to_iso(episode.finished_at)

        duration_seconds: Optional[float] = None
        if episode.started_at and episode.finished_at:
            try:
                start_dt = (
                    episode.started_at
                    if isinstance(episode.started_at, datetime.datetime)
                    else datetime.datetime.fromisoformat(str(episode.started_at))
                )
                end_dt = (
                    episode.finished_at
                    if isinstance(episode.finished_at, datetime.datetime)
                    else datetime.datetime.fromisoformat(str(episode.finished_at))
                )
                duration_seconds = max(0.0, (end_dt - start_dt).total_seconds())
            except Exception:
                duration_seconds = None

        agent_payload_dict: Optional[Dict[str, Any]] = None
        if episode.agent_payload is not None:
            if hasattr(episode.agent_payload, "model_dump"):
                agent_payload_dict = episode.agent_payload.model_dump()
            elif isinstance(episode.agent_payload, dict):
                agent_payload_dict = episode.agent_payload

        return {
            "episode_id": episode.episode_id,
            "scenario_id": episode.scenario_id,
            "status": episode.status.value,
            "created_at": created_at_iso,
            "started_at": started_at_iso,
            "finished_at": finished_at_iso,
            "duration_seconds": duration_seconds,
            "step_count": episode.step_count,
            "agent_payload": agent_payload_dict,
            "fault_results": episode.fault_results,
            "tool_calls": episode.tool_calls,
            "evaluation_results": episode.evaluation_results,
            "errors": {
                "error": episode.error,
                "setup_error": episode.setup_error,
                "agent_error": episode.agent_error,
                "cleanup_error": episode.cleanup_error,
            },
            "metadata": episode.metadata,
        }

    def record(self, episode: Episode) -> Path:
        """Serialize and save an episode record to a formatted JSON file."""
        data = self.serialize_episode(episode)
        filename = f"{episode.scenario_id}_{episode.episode_id}.json"
        target_path = self.records_dir / filename

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info("Saved episode record to %s", target_path)
        return target_path

    def load(self, path: Union[str, Path]) -> Dict[str, Any]:
        """Load an episode record from JSON file."""
        target_path = Path(path)
        if not target_path.exists():
            raise FileNotFoundError(f"Record file not found: {target_path}")

        with open(target_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_records(self) -> List[Path]:
        """Return list of all recorded JSON files in records_dir sorted by modification time."""
        if not self.records_dir.exists():
            return []
        records = list(self.records_dir.glob("*.json"))
        records.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return records
