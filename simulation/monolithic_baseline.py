"""
Monolithic Single-Agent Baseline
A single LLM handles all customer support functions without task-specific specialization.
Used as the control condition for comparative evaluation.
"""
import time
from typing import Dict, Optional
from app.agents.llm_client import LLMClient


MONOLITHIC_SYSTEM_PROMPT = """You are a general customer support assistant for a Thai e-commerce store.
You handle ALL types of customer inquiries: order status, returns, refunds, shipping, store policies.

Instructions:
1. Answer all customer questions to the best of your ability
2. Use polite Thai language (use ค่ะ for female tone)
3. If you don't know the answer, say so politely
4. Keep responses concise (2-4 sentences)
5. You do NOT have access to any external databases or tools
6. Do NOT make up order information — if you don't have access to the data, say so

You are a SINGLE general-purpose agent handling everything. You do NOT delegate to specialized agents.
"""


class MonolithicBaseline:
    """Single-agent baseline — one LLM handles all inquiries."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.system_prompt = MONOLITHIC_SYSTEM_PROMPT
        self.sessions = {}

    def process_message(self, message: str, session_id: str = None) -> Dict:
        """Process a customer message with a single monolithic agent."""
        import uuid
        if not session_id:
            session_id = str(uuid.uuid4())[:8]

        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "history": [],
                "turn_count": 0,
            }

        session = self.sessions[session_id]
        session["turn_count"] += 1

        # Build conversation context
        conversation = ""
        for h in session["history"][-4:]:
            conversation += f"{h['role']}: {h['content']}\n"
        conversation += f"customer: {message}"

        start = time.time()
        result = self.llm.chat(self.system_prompt, conversation)
        latency = time.time() - start

        session["history"].append({"role": "customer", "content": message})
        session["history"].append({"role": "assistant", "content": result["content"]})

        return {
            "session_id": session_id,
            "response": result["content"],
            "latency": round(latency, 3),
            "tokens": result["tokens"],
            "turn": session["turn_count"],
        }

    def reset(self):
        """Reset all sessions."""
        self.sessions = {}
