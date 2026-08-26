# /api/game/* router + /game page (任务书 G12-G15).
#
# Session state lives in-process only; every mutation carries
# expected_version (optimistic concurrency); illegal strategy actions are
# rejected receipts (HTTP 200), transport errors use stable codes.
import os

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

try:
    from .game_service import GameError, GameSessionStore, MIN_ROUNDS_DEFAULT
except ImportError:                       # run as top-level modules (app-dir web)
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from game_service import GameError, GameSessionStore, MIN_ROUNDS_DEFAULT

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_router(store: GameSessionStore) -> APIRouter:
    r = APIRouter(prefix="/api/game")

    class CreateReq(BaseModel):
        replay_id: str
        opponent_player: int
        battle_seed: int | None = None
        min_rounds: int = MIN_ROUNDS_DEFAULT

    class CommandReq(BaseModel):
        expected_version: int
        kind: str
        payload: dict = {}

    @r.get("/replays")
    def replays(min_rounds: int = MIN_ROUNDS_DEFAULT):
        return store.library.summary(min_rounds=min_rounds)

    @r.post("/sessions")
    def create_session(req: CreateReq):
        sess = store.create(req.replay_id, req.opponent_player,
                            battle_seed_base=req.battle_seed,
                            min_rounds=req.min_rounds)
        return sess.view()

    @r.get("/sessions/{sid}")
    def get_session(sid: str):
        return store.get(sid).view()

    @r.post("/sessions/{sid}/commands")
    def command(sid: str, req: CommandReq):
        sess = store.get(sid)
        view, receipt = sess.execute(req.expected_version, req.kind, req.payload)
        out = dict(view)
        if receipt is not None:
            out["rejected_receipt"] = receipt
        return out

    @r.delete("/sessions/{sid}")
    def delete_session(sid: str):
        store.delete(sid)
        return {"deleted": sid}

    @r.get("/sessions")   # debug: id list (no state)
    def list_sessions():
        return {"count": store.count()}

    return r


def game_page():
    return FileResponse(os.path.join(ROOT, "web", "static", "game.html"))


def game_error_handler(exc: GameError):
    return JSONResponse(status_code=exc.http_status,
                        content={"error": exc.code, "detail": exc.detail})
