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
    from hourplan.admin import routes as admin_routes
    from hourplan.ailog import mcp_routes
    from hourplan.ailog import routes as ailog_routes
    from hourplan.auth import cli as auth_cli
    from hourplan.auth import routes as auth_routes
    from hourplan.health import routes as health_routes
    from hourplan.imports import cli as imports_cli
    from hourplan.main import routes as main_routes
    from hourplan.planning import routes as planning_routes
    from hourplan.projects import routes as project_routes
    from hourplan.reports import routes as report_routes
    from hourplan.resources import cli as resources_cli
    from hourplan.setup import cli as setup_cli
    from hourplan.shared import i18n
    from hourplan.shared.errors import init_error_handlers
    from hourplan.shared.security import init_security_headers
    from hourplan.timer import routes as timer_routes
    from hourplan.timesheet import absence_routes, bank_routes
    from hourplan.timesheet import routes as timesheet_routes
    from hourplan.voice import cli as voice_cli
    from hourplan.voice import routes as voice_routes

    extensions.db.init_app(app)
    extensions.csrf.init_app(app)
    extensions.limiter.init_app(app)
    extensions.login_manager.init_app(app)
    extensions.login_manager.login_view = "auth.login"
    extensions.login_manager.login_message = "Inicia sesión para continuar."
    extensions.login_manager.localize_callback = i18n._
    i18n.init_app(app)
    extensions.login_manager.session_protection = "strong"

    app.register_blueprint(health_routes.bp)
    app.register_blueprint(auth_routes.bp)
    app.register_blueprint(auth_cli.bp)
    app.register_blueprint(resources_cli.bp)
    app.register_blueprint(main_routes.bp)
    app.register_blueprint(admin_routes.bp)
    app.register_blueprint(timesheet_routes.bp)
    app.register_blueprint(absence_routes.bp)
    app.register_blueprint(bank_routes.bp)
    app.register_blueprint(timer_routes.bp)
    app.register_blueprint(voice_routes.bp)
    app.register_blueprint(ailog_routes.bp)
    app.register_blueprint(mcp_routes.bp)
    app.register_blueprint(voice_cli.bp)
    app.register_blueprint(project_routes.bp)
    app.register_blueprint(planning_routes.bp)
    app.register_blueprint(report_routes.bp)
    app.register_blueprint(setup_cli.bp)
    app.register_blueprint(imports_cli.bp)

    init_error_handlers(app)
    init_security_headers(app)
    return app
