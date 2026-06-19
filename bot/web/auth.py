from __future__ import annotations

import time
import secrets
from typing import Dict, Optional

# Maps login_token -> {"tg_id": int, "expires_at": float}
login_tokens: Dict[str, dict] = {}

# Maps session_token -> {"tg_id": int, "expires_at": float}
web_sessions: Dict[str, dict] = {}

def generate_login_token(tg_id: int) -> str:
    token = secrets.token_hex(16)
    login_tokens[token] = {
        "tg_id": tg_id,
        "expires_at": time.time() + 300  # 5 minutes
    }
    return token

def verify_login_token(token: str) -> Optional[int]:
    # clean expired tokens
    now = time.time()
    expired = [k for k, v in login_tokens.items() if v["expires_at"] < now]
    for k in expired:
        login_tokens.pop(k, None)
        
    data = login_tokens.pop(token, None)
    if data and data["expires_at"] >= now:
        return data["tg_id"]
    return None

def create_session(tg_id: int) -> str:
    session_token = secrets.token_hex(32)
    web_sessions[session_token] = {
        "tg_id": tg_id,
        "expires_at": time.time() + 86400  # 24 hours
    }
    return session_token

def verify_session(session_token: str) -> Optional[int]:
    now = time.time()
    # clean expired sessions
    expired = [k for k, v in web_sessions.items() if v["expires_at"] < now]
    for k in expired:
        web_sessions.pop(k, None)
        
    data = web_sessions.get(session_token)
    if data and data["expires_at"] >= now:
        # extend session on active use
        data["expires_at"] = now + 86400
        return data["tg_id"]
    return None
