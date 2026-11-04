"""Flask extensions, created here and initialised in ``create_app``."""

from flask import Flask, g
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_wtf import CSRFProtect
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


class Database:
    """Engine plus one session per application context."""

    def __init__(self) -> None:
        self.engine: Engine | None = None
        self.session_factory: sessionmaker[Session] | None = None

    def init_app(self, app: Flask) -> None:
        self.engine = create_engine(app.config["DATABASE_URL"], pool_pre_ping=True)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
        app.teardown_appcontext(self._close_session)

    @property
    def session(self) -> Session:
        if "db_session" not in g:
            if self.session_factory is None:
                raise RuntimeError("Database is not initialised")
            g.db_session = self.session_factory()
        session: Session = g.db_session
        return session

    @staticmethod
    def _close_session(_exc: BaseException | None = None) -> None:
        session: Session | None = g.pop("db_session", None)
        if session is not None:
            session.close()


db = Database()
csrf = CSRFProtect()
login_manager = LoginManager()
limiter = Limiter(key_func=get_remote_address)
