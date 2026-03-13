"""Memory system for EmberOS-Windows: SQLite + ChromaDB + context window management."""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("emberos.memory")


class ConversationStore:
    """SQLite-backed persistent conversation log."""

    def __init__(self, db_path: str):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self._create_table()

    def _create_table(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_calls TEXT,
                context_snapshot TEXT,
                is_summary INTEGER DEFAULT 0
            )
        """)
        self._conn.commit()

    def add(self, session_id: str, role: str, content: str,
            tool_calls: Optional[str] = None, context_snapshot: Optional[str] = None,
            is_summary: bool = False) -> int:
        with self._lock:
            ts = datetime.now(timezone.utc).isoformat()
            cur = self._conn.execute(
                "INSERT INTO conversations (session_id, timestamp, role, content, tool_calls, context_snapshot, is_summary) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, ts, role, content, tool_calls, context_snapshot, 1 if is_summary else 0),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_recent(self, session_id: str, n: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, session_id, timestamp, role, content, tool_calls, context_snapshot, is_summary "
                "FROM conversations WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, n),
            ).fetchall()
        rows.reverse()  # chronological order
        return [self._row_to_dict(r) for r in rows]

    def get_all_sessions(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT session_id FROM conversations"
            ).fetchall()
        return [r[0] for r in rows]

    def get_session_count(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return row[0]

    def mark_as_summarized(self, row_ids: list[int]):
        if not row_ids:
            return
        with self._lock:
            placeholders = ",".join("?" for _ in row_ids)
            self._conn.execute(
                f"DELETE FROM conversations WHERE id IN ({placeholders})",
                row_ids,
            )
            self._conn.commit()

    def search_keyword(self, query: str, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, session_id, timestamp, role, content, tool_calls, context_snapshot, is_summary "
                "FROM conversations WHERE content LIKE ? ORDER BY timestamp DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def enforce_max(self, max_total: int = 10000):
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            if count > max_total:
                excess = count - max_total
                self._conn.execute(
                    "DELETE FROM conversations WHERE id IN "
                    "(SELECT id FROM conversations ORDER BY id ASC LIMIT ?)",
                    (excess,),
                )
                self._conn.commit()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "id": row[0],
            "session_id": row[1],
            "timestamp": row[2],
            "role": row[3],
            "content": row[4],
            "tool_calls": row[5],
            "context_snapshot": row[6],
            "is_summary": bool(row[7]),
        }


class VectorStore:
    """ChromaDB-backed semantic search over past conversations."""

    def __init__(self, vector_path: str, cache_dir: str = "data/models/sentence_transformer"):
        path = Path(vector_path)
        path.mkdir(parents=True, exist_ok=True)
        cache = Path(cache_dir)
        cache.mkdir(parents=True, exist_ok=True)

        import chromadb
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

        os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(cache))
        self._ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        self._client = chromadb.PersistentClient(path=str(path))
        self._collection = self._client.get_or_create_collection(
            name="ember_conversations",
            embedding_function=self._ef,
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, doc_id: str, text: str, metadata: dict):
        self._collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata],
        )

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        results = self._collection.query(
            query_texts=[query],
            n_results=min(top_k, max(self.get_count(), 1)),
        )
        out = []
        if results and results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                out.append({
                    "id": doc_id,
                    "text": results["documents"][0][i] if results["documents"] else "",
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "distance": results["distances"][0][i] if results["distances"] else 0.0,
                })
        return out

    def delete(self, doc_ids: list[str]):
        if doc_ids:
            self._collection.delete(ids=doc_ids)

    def get_count(self) -> int:
        return self._collection.count()


class ContextWindowManager:
    """Manages what gets sent to the LLM, summarizes old turns to prevent overflow."""

    def __init__(self, config):
        self.max_context_tokens = getattr(config, "max_context_tokens", 3500)
        # BitNet on this Windows build becomes unstable with larger prompts, so
        # keep the effective prompt budget conservative even if the logical
        # context window is larger.
        self.prompt_token_budget = min(self.max_context_tokens, 220)
        self.summarize_after_turns = getattr(config, "summarize_after_turns", 10)
        self.turns_to_keep_verbatim = getattr(config, "turns_to_keep_verbatim", 6)
        self.conv_store: Optional[ConversationStore] = None
        self.vector_store: Optional[VectorStore] = None

    def bind_stores(self, conv_store: ConversationStore, vector_store: VectorStore):
        self.conv_store = conv_store
        self.vector_store = vector_store

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        word_estimate = int(len(text.split()) * 1.3)
        char_estimate = (len(text) + 2) // 3
        return max(word_estimate, char_estimate)

    def get_messages_for_llm(self, session_id: str, system_prompt: str,
                             new_user_message: str, llm_client) -> list[dict]:
        recent = self.conv_store.get_recent(session_id, n=50)

        # Split into verbatim (last N turns) and older
        verbatim = recent[-self.turns_to_keep_verbatim:]
        older = recent[:-self.turns_to_keep_verbatim] if len(recent) > self.turns_to_keep_verbatim else []

        # Build verbatim messages
        verbatim_msgs = []
        for row in verbatim:
            role = row["role"]
            if role == "tool_result":
                role = "user"
            if role not in ("user", "assistant", "system"):
                role = "user"
            verbatim_msgs.append({"role": role, "content": row["content"]})

        # Calculate token budget
        system_tokens = self._estimate_tokens(system_prompt)
        new_msg_tokens = self._estimate_tokens(new_user_message)
        verbatim_tokens = sum(self._estimate_tokens(m["content"]) for m in verbatim_msgs)
        used = system_tokens + new_msg_tokens + verbatim_tokens
        remaining = self.prompt_token_budget - used

        # For the local BitNet backend, dropping history is more reliable than
        # summarizing, because a summary request can be large enough to crash it.
        if remaining < 0:
            while verbatim_msgs and self._estimate_tokens(system_prompt) + new_msg_tokens + sum(
                self._estimate_tokens(m["content"]) for m in verbatim_msgs
            ) > self.prompt_token_budget:
                verbatim_msgs.pop(0)
            older_msgs = []
        else:
            # Add older turns going backwards until budget is exhausted
            older_msgs = []
            for row in reversed(older):
                row_tokens = self._estimate_tokens(row["content"])
                if row_tokens > remaining:
                    break
                role = row["role"]
                if role not in ("user", "assistant", "system"):
                    role = "user"
                older_msgs.insert(0, {"role": role, "content": row["content"]})
                remaining -= row_tokens

        # Assemble final messages: system → older → verbatim → new user message
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(older_msgs)
        messages.extend(verbatim_msgs)
        messages.append({"role": "user", "content": new_user_message})
        return messages

    def summarize_old_turns(self, session_id: str, turns: list[dict], llm_client) -> str:
        transcript_lines = []
        for t in turns:
            transcript_lines.append(f"{t['role']}: {t['content']}")
        transcript = "\n".join(transcript_lines)

        prompt = (
            "Summarize the following conversation history into a dense, factual paragraph "
            "that preserves all key decisions, facts, file paths, tool results, and user "
            "preferences mentioned. This summary will replace the original turns in context. "
            "Be concise but lose nothing important.\n\n"
            + transcript
        )

        try:
            summary_text = llm_client.chat([
                {"role": "system", "content": "You are a helpful summarizer."},
                {"role": "user", "content": prompt},
            ])
        except Exception as e:
            logger.error("Summarization LLM call failed: %s", e)
            # Fallback: just take last portion of transcript
            summary_text = transcript[-500:]

        tagged = "[CONVERSATION SUMMARY] " + summary_text
        row_ids = [t["id"] for t in turns if "id" in t]

        # Store summary row
        new_id = self.conv_store.add(
            session_id, "system", tagged, is_summary=True,
        )

        # Clean up old rows from SQLite
        self.conv_store.mark_as_summarized(row_ids)

        # Update vector store
        if self.vector_store:
            self.vector_store.delete([str(rid) for rid in row_ids])
            ts = datetime.now(timezone.utc).isoformat()
            self.vector_store.add(
                str(new_id), summary_text,
                {"session_id": session_id, "role": "system", "timestamp": ts},
            )

        return tagged

    def maybe_trigger_summarization(self, session_id: str, llm_client):
        try:
            count = self.conv_store.get_session_count(session_id)
            if count > self.summarize_after_turns and count % self.summarize_after_turns == 0:
                recent = self.conv_store.get_recent(session_id, n=count)
                older = recent[:-self.turns_to_keep_verbatim] if len(recent) > self.turns_to_keep_verbatim else []
                if older:
                    older_tokens = sum(self._estimate_tokens(r["content"]) for r in older)
                    if older_tokens > self.max_context_tokens * 0.5:
                        half = len(older) // 2
                        to_summarize = older[:half]
                        if to_summarize:
                            self.summarize_old_turns(session_id, to_summarize, llm_client)
        except Exception:
            logger.exception("Background summarization failed")
