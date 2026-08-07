"""
Session Management Module
Maintains conversation state, interaction logs, and experiment metadata.
"""
import json
import csv
import time
import os
from typing import Dict, List, Optional, Any
from datetime import datetime
from app.config import LOGS_DIR


class SessionManager:
    """Manages conversation sessions and experiment logging."""

    def __init__(self):
        self._sessions: Dict[str, Dict] = {}
        self.logs_dir = LOGS_DIR
        os.makedirs(self.logs_dir, exist_ok=True)

    def create_session(self, session_id: str) -> Dict:
        """Create a new conversation session."""
        self._sessions[session_id] = {
            "session_id": session_id,
            "created_at": datetime.now().isoformat(),
            "messages": [],
            "state": {
                "order_ids": [],
                "resolved_topics": [],
                "turn_count": 0,
                "last_intent": None,
                "escalated": False,
                # Task 8A — persistent order context + workflow bookkeeping.
                # active_order_id survives informational turns (greeting,
                # payment/shipment/policy answers) and is only cleared by an
                # explicit remove-order-context, a chat reset, or a new
                # explicit order replacing it.
                "active_order_id": None,
                "pending_intent": None,
                "pending_subtype": None,
                "missing_slots": [],
                "collected_slots": {},
                "last_agent": None,
                "last_completed_intent": None,
                "last_assistant_action": None,
            },
            "experiment_log": [],
        }
        return self._sessions[session_id]

    def get_session(self, session_id: str) -> Optional[Dict]:
        """Get existing session or create new."""
        if session_id not in self._sessions:
            return self.create_session(session_id)
        return self._sessions[session_id]

    def add_message(
        self, session_id: str, role: str, content: str,
        metadata: Dict = None
    ):
        """Add a message to the conversation history."""
        session = self.get_session(session_id)
        entry = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "turn": session["state"]["turn_count"],
        }
        if metadata:
            entry["metadata"] = metadata
        session["messages"].append(entry)

    def log_experiment_event(
        self, session_id: str, event_type: str, data: Dict
    ):
        """Log an experiment event (routing decision, agent result, etc.)."""
        session = self.get_session(session_id)
        entry = {
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            "turn": session["state"]["turn_count"],
            **data,
        }
        session["experiment_log"].append(entry)

    def update_state(self, session_id: str, updates: Dict):
        """Update session state fields."""
        session = self.get_session(session_id)
        session["state"].update(updates)
        session["state"]["turn_count"] = len(
            [m for m in session["messages"] if m["role"] == "customer"]
        )

    def get_context(self, session_id: str) -> Dict:
        """Get conversation context for agent consumption."""
        session = self.get_session(session_id)
        messages = session.get("messages", [])
        return {
            "session_id": session_id,
            "previous_messages": [
                f"{m['role']}: {m['content']}"
                for m in messages[-6:]  # Last 6 messages for context
            ],
            "state": session["state"],
        }

    def export_session_log(self, session_id: str, format: str = "json") -> str:
        """Export session log to file."""
        session = self.get_session(session_id)
        filename = f"session_{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        if format == "json":
            path = os.path.join(self.logs_dir, f"{filename}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(session, f, ensure_ascii=False, indent=2)
        elif format == "csv":
            path = os.path.join(self.logs_dir, f"{filename}.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "turn", "role", "content", "intent", "latency", "event_type"
                ])
                writer.writeheader()
                for msg in session.get("messages", []):
                    writer.writerow({
                        "turn": msg.get("turn", 0),
                        "role": msg.get("role", ""),
                        "content": msg.get("content", ""),
                        "intent": msg.get("metadata", {}).get("intent", ""),
                        "latency": msg.get("metadata", {}).get("latency", 0),
                        "event_type": "message",
                    })
                for evt in session.get("experiment_log", []):
                    writer.writerow({
                        "turn": evt.get("turn", 0),
                        "role": "system",
                        "content": json.dumps(evt, ensure_ascii=False),
                        "event_type": evt.get("event_type", ""),
                    })

        return path

    def cleanup_old_sessions(self, max_age_minutes: int = 60):
        """Remove stale sessions from memory."""
        now = datetime.now()
        stale_ids = []
        for sid, session in self._sessions.items():
            created = datetime.fromisoformat(session["created_at"])
            if (now - created).total_seconds() > max_age_minutes * 60:
                stale_ids.append(sid)
        for sid in stale_ids:
            del self._sessions[sid]
        return len(stale_ids)
