"""HourPlan application factory."""

from typing import Any

from flask import Flask

from hourplan.config import load_config


def create_app(config_name: str | None = None, overrides: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(load_config(config_name))
    if overrides:
        app.config.from_mapping(overrides)

    from hourplan import extensions
    from hourplan.auth import cli as auth_cli
    from hourplan.auth import routes as auth_routes
    from hourplan.health import routes as health_routes
    from hourplan.main import routes as main_routes
    from hourplan.setup import cli as setup_cli
    from hourplan.shared.errors import init_error_handlers
    from hourplan.shared.security import init_security_headers

    extensions.db.init_app(app)
    extensions.csrf.init_app(app)
    extensions.limiter.init_app(app)
    extensions.login_manager.init_app(app)
    extensions.login_manager.login_view = "auth.login"
    extensions.login_manager.login_message = "Inicia sesión para continuar."
    extensions.login_manager.session_protection = "strong"

    app.register_blueprint(health_routes.bp)
    app.register_blueprint(auth_routes.bp)
    app.register_blueprint(auth_cli.bp)
    app.register_blueprint(main_routes.bp)
    app.register_blueprint(setup_cli.bp)

    init_error_handlers(app)
    init_security_headers(app)
    return app
