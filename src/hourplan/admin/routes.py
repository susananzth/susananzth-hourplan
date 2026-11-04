"""Administration screens. Every view is restricted by role on the server."""

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from pydantic import ValidationError
from werkzeug.wrappers import Response

from hourplan.admin import forms
from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.permissions import roles_required
from hourplan.auth.roles import Role
from hourplan.catalogs import services as catalogs
from hourplan.catalogs.models import (
    CHILD_KINDS,
    Application,
    CatalogItem,
    CatalogKind,
    Portfolio,
    Repository,
)
from hourplan.extensions import db
from hourplan.policies import services as policies
from hourplan.policies.models import EvaluationPolicy, WorkPolicy

bp = Blueprint("admin", __name__, url_prefix="/admin", template_folder="templates")

CATALOG_ROLES = (Role.ADMIN, Role.PM)

KIND_LABELS = {
    CatalogKind.REQUIREMENT_TYPE: "Tipos de requerimiento",
    CatalogKind.TASK_TYPE: "Tipos de tarea",
    CatalogKind.PROJECT_STATUS: "Estados de proyecto",
    CatalogKind.PRIORITY: "Prioridades",
    CatalogKind.REWORK_CATEGORY: "Categorías de retrabajo",
    CatalogKind.REWORK_REASON: "Motivos de retrabajo",
    CatalogKind.ACTIVITY_BUCKET: "Bolsas de actividad",
    CatalogKind.APPLICATION_TYPE: "Tipos de aplicación",
    CatalogKind.CHANGE_REQUEST_SIZE: "Tamaños de solicitud de cambio",
}
# Attribute flags editable per kind.
KIND_FLAGS = {
    CatalogKind.PROJECT_STATUS: ["is_closed"],
    CatalogKind.REWORK_CATEGORY: ["attributable_to_developer"],
    CatalogKind.ACTIVITY_BUCKET: ["is_contingency"],
}
PARENT_KIND = {child: parent for parent, child in CHILD_KINDS.items()}


@bp.app_context_processor
def navigation() -> dict[str, Any]:
    """Admin links visible to the current user (the views enforce the roles again)."""
    links: list[tuple[str, str]] = []
    if current_user.is_authenticated:
        if current_user.has_role(*CATALOG_ROLES):
            links += [
                (url_for("admin.catalog_list"), "Catálogos"),
                (url_for("admin.portfolio_list"), "Carteras"),
                (url_for("admin.application_list"), "Aplicaciones"),
                (url_for("admin.repository_list"), "Repositorios"),
            ]
        if current_user.has_role(Role.ADMIN):
            links += [
                (url_for("admin.user_list"), "Usuarios"),
                (url_for("admin.policy_list"), "Políticas"),
            ]
    return {"nav_links": links}


def _today() -> date:
    return datetime.now().astimezone().date()


def _actor() -> int:
    return int(current_user.id)


def _fail(err: Exception) -> None:
    flash(_message(err), "error")


def _message(err: Exception) -> str:
    match err:
        case catalogs.DuplicateError():
            return "Ya existe un registro con ese código o nombre."
        case auth.EmailAlreadyRegisteredError():
            return "Ya existe un usuario con ese correo."
        case auth.WeakPasswordError():
            return "La contraseña debe tener al menos 12 caracteres."
        case auth.SelfLockoutError():
            return "No puedes quitarte tu propio acceso de administración."
        case policies.PolicyInForceError():
            return "La política ya está vigente o la fecha es pasada: elige una fecha futura."
        case ValidationError():
            return "Revisa los valores: " + "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors()
            )
        case _:
            return "No se pudo guardar: revisa los datos."


def _get_or_404[T](model: type[T], entity_id: int) -> T:
    entity = db.session.get(model, entity_id)
    if entity is None:
        abort(404)
    return entity


def _options(items: list[Any], *, empty: str) -> list[tuple[int, str]]:
    return [(0, empty)] + [(i.id, i.name) for i in items if i.is_active]


def _text(field: Any) -> str:
    return str(field.data or "")


def _none_if_zero(value: int | None) -> int | None:
    return value or None


def _form_page(form: Any, title: str, back: str, *, code_locked: bool = False) -> str:
    return render_template(
        "admin/form.html", form=form, title=title, back=back, code_locked=code_locked
    )


def _save(action: Callable[[], object], back: str) -> Response | None:
    """Run a service call; return a redirect on success or ``None`` after flashing the error."""
    try:
        action()
    except (catalogs.CatalogError, auth.AuthError, policies.PolicyError, ValidationError) as err:
        db.session.rollback()
        _fail(err)
        return None
    flash("Cambios guardados.", "info")
    return redirect(back)


def _toggle(entity: Any, setter: Callable[..., object], back: str) -> Response:
    setter(db.session, entity, not entity.is_active, actor_id=_actor())
    flash("Registro desactivado." if not entity.is_active else "Registro activado.", "info")
    return redirect(back)


# --- Catalogs ----------------------------------------------------------------------------------


@bp.get("/catalogos")
@roles_required(*CATALOG_ROLES)
def catalog_list() -> str:
    kind = _kind_arg(request.args.get("kind"))
    return render_template(
        "admin/catalog_list.html",
        kinds=KIND_LABELS,
        kind=kind,
        items=catalogs.list_items(db.session, kind),
    )


def _kind_arg(value: str | None) -> CatalogKind:
    try:
        return CatalogKind(value) if value else next(iter(CatalogKind))
    except ValueError:
        abort(404)


def _catalog_form(kind: CatalogKind, item: CatalogItem | None = None) -> forms.CatalogItemForm:
    form = forms.CatalogItemForm(obj=item)
    flags = KIND_FLAGS.get(kind, [])
    for flag in ("is_closed", "attributable_to_developer", "is_contingency"):
        if flag not in flags:
            delattr(form, flag)
    if item is not None and not form.is_submitted():
        for flag in flags:
            getattr(form, flag).data = item.attributes.get(flag, getattr(form, flag).data)
    parent_kind = PARENT_KIND.get(kind)
    if parent_kind is None or item is not None:  # the parent is fixed once created
        delattr(form, "parent_id")
    else:
        parents = catalogs.list_items(db.session, parent_kind)
        form.parent_id.choices = _options(parents, empty="Sin padre")
    if item is not None:
        del form.code
    return form


def _attributes(form: forms.CatalogItemForm, kind: CatalogKind) -> dict[str, Any]:
    return {flag: bool(getattr(form, flag).data) for flag in KIND_FLAGS.get(kind, [])}


@bp.route("/catalogos/nuevo", methods=["GET", "POST"])
@roles_required(*CATALOG_ROLES)
def catalog_new() -> Response | str:
    kind = _kind_arg(request.args.get("kind"))
    form = _catalog_form(kind)
    back = url_for("admin.catalog_list", kind=kind.value)
    if form.validate_on_submit():
        parent = form.parent_id.data if "parent_id" in form else None
        done = _save(
            lambda: catalogs.create_item(
                db.session,
                kind=kind,
                code=_text(form.code),
                name=_text(form.name),
                parent_id=_none_if_zero(parent),
                sort_order=form.sort_order.data or 0,
                attributes=_attributes(form, kind),
                actor_id=_actor(),
            ),
            back,
        )
        if done:
            return done
    return _form_page(form, f"Nuevo · {KIND_LABELS[kind]}", back)


@bp.route("/catalogos/<int:item_id>", methods=["GET", "POST"])
@roles_required(*CATALOG_ROLES)
def catalog_edit(item_id: int) -> Response | str:
    item = _get_or_404(CatalogItem, item_id)
    kind = CatalogKind(item.kind)
    form = _catalog_form(kind, item)
    back = url_for("admin.catalog_list", kind=kind.value)
    if form.validate_on_submit():
        done = _save(
            lambda: catalogs.update_item(
                db.session,
                item,
                name=_text(form.name),
                sort_order=form.sort_order.data or 0,
                attributes=_attributes(form, kind),
                actor_id=_actor(),
            ),
            back,
        )
        if done:
            return done
    return _form_page(form, f"Editar {item.code}", back)


@bp.post("/catalogos/<int:item_id>/activo")
@roles_required(*CATALOG_ROLES)
def catalog_toggle(item_id: int) -> Response:
    item = _get_or_404(CatalogItem, item_id)
    return _toggle(item, catalogs.set_item_active, url_for("admin.catalog_list", kind=item.kind))


# --- Portfolios, applications and repositories ---------------------------------------------------


@bp.get("/carteras")
@roles_required(*CATALOG_ROLES)
def portfolio_list() -> str:
    rows = [(p, [p.code, p.name, p.sort_order]) for p in catalogs.list_portfolios(db.session)]
    return _simple_list("Carteras", ["Código", "Nombre", "Orden"], rows, "portfolio")


@bp.route("/carteras/nueva", methods=["GET", "POST"])
@roles_required(*CATALOG_ROLES)
def portfolio_new() -> Response | str:
    form = forms.PortfolioForm()
    back = url_for("admin.portfolio_list")
    if form.validate_on_submit():
        done = _save(
            lambda: catalogs.create_portfolio(
                db.session,
                code=_text(form.code),
                name=_text(form.name),
                sort_order=form.sort_order.data or 0,
                actor_id=_actor(),
            ),
            back,
        )
        if done:
            return done
    return _form_page(form, "Nueva cartera", back)


@bp.route("/carteras/<int:entity_id>", methods=["GET", "POST"])
@roles_required(*CATALOG_ROLES)
def portfolio_edit(entity_id: int) -> Response | str:
    portfolio = _get_or_404(Portfolio, entity_id)
    form = forms.PortfolioForm(obj=portfolio)
    del form.code
    back = url_for("admin.portfolio_list")
    if form.validate_on_submit():
        done = _save(
            lambda: catalogs.update_portfolio(
                db.session,
                portfolio,
                name=_text(form.name),
                sort_order=form.sort_order.data or 0,
                actor_id=_actor(),
            ),
            back,
        )
        if done:
            return done
    return _form_page(form, f"Editar {portfolio.code}", back)


@bp.post("/carteras/<int:entity_id>/activo")
@roles_required(*CATALOG_ROLES)
def portfolio_toggle(entity_id: int) -> Response:
    return _toggle(
        _get_or_404(Portfolio, entity_id),
        catalogs.set_portfolio_active,
        url_for("admin.portfolio_list"),
    )


def _application_form(application: Application | None = None) -> forms.ApplicationForm:
    form = forms.ApplicationForm(obj=application)
    types = catalogs.list_items(db.session, CatalogKind.APPLICATION_TYPE)
    form.type_id.choices = _options(types, empty="Sin tipo")
    if application is not None:
        del form.code
        if not form.is_submitted():
            form.type_id.data = application.type_id or 0
    return form


@bp.get("/aplicaciones")
@roles_required(*CATALOG_ROLES)
def application_list() -> str:
    types = {i.id: i.name for i in catalogs.list_items(db.session, CatalogKind.APPLICATION_TYPE)}
    rows = [
        (a, [a.code, a.name, types.get(a.type_id or 0, "—")])
        for a in catalogs.list_applications(db.session)
    ]
    return _simple_list("Aplicaciones", ["Código", "Nombre", "Tipo"], rows, "application")


@bp.route("/aplicaciones/nueva", methods=["GET", "POST"])
@roles_required(*CATALOG_ROLES)
def application_new() -> Response | str:
    form = _application_form()
    back = url_for("admin.application_list")
    if form.validate_on_submit():
        done = _save(
            lambda: catalogs.create_application(
                db.session,
                code=_text(form.code),
                name=_text(form.name),
                type_id=_none_if_zero(form.type_id.data),
                actor_id=_actor(),
            ),
            back,
        )
        if done:
            return done
    return _form_page(form, "Nueva aplicación", back)


@bp.route("/aplicaciones/<int:entity_id>", methods=["GET", "POST"])
@roles_required(*CATALOG_ROLES)
def application_edit(entity_id: int) -> Response | str:
    application = _get_or_404(Application, entity_id)
    form = _application_form(application)
    back = url_for("admin.application_list")
    if form.validate_on_submit():
        done = _save(
            lambda: catalogs.update_application(
                db.session,
                application,
                name=_text(form.name),
                type_id=_none_if_zero(form.type_id.data),
                actor_id=_actor(),
            ),
            back,
        )
        if done:
            return done
    return _form_page(form, f"Editar {application.code}", back)


@bp.post("/aplicaciones/<int:entity_id>/activo")
@roles_required(*CATALOG_ROLES)
def application_toggle(entity_id: int) -> Response:
    return _toggle(
        _get_or_404(Application, entity_id),
        catalogs.set_application_active,
        url_for("admin.application_list"),
    )


def _repository_form(repository: Repository | None = None) -> forms.RepositoryForm:
    form = forms.RepositoryForm(obj=repository)
    form.application_id.choices = _options(
        catalogs.list_applications(db.session), empty="Sin aplicación"
    )
    if repository is not None and not form.is_submitted():
        form.application_id.data = repository.application_id or 0
    return form


@bp.get("/repositorios")
@roles_required(*CATALOG_ROLES)
def repository_list() -> str:
    names = {a.id: a.name for a in catalogs.list_applications(db.session)}
    rows = [
        (r, [r.name, r.url or "—", names.get(r.application_id or 0, "—")])
        for r in catalogs.list_repositories(db.session)
    ]
    return _simple_list("Repositorios", ["Nombre", "URL", "Aplicación"], rows, "repository")


@bp.route("/repositorios/nuevo", methods=["GET", "POST"])
@roles_required(*CATALOG_ROLES)
def repository_new() -> Response | str:
    form = _repository_form()
    back = url_for("admin.repository_list")
    if form.validate_on_submit():
        done = _save(
            lambda: catalogs.create_repository(
                db.session,
                name=_text(form.name),
                url=form.url.data,
                application_id=_none_if_zero(form.application_id.data),
                actor_id=_actor(),
            ),
            back,
        )
        if done:
            return done
    return _form_page(form, "Nuevo repositorio", back)


@bp.route("/repositorios/<int:entity_id>", methods=["GET", "POST"])
@roles_required(*CATALOG_ROLES)
def repository_edit(entity_id: int) -> Response | str:
    repository = _get_or_404(Repository, entity_id)
    form = _repository_form(repository)
    back = url_for("admin.repository_list")
    if form.validate_on_submit():
        done = _save(
            lambda: catalogs.update_repository(
                db.session,
                repository,
                name=_text(form.name),
                url=form.url.data,
                application_id=_none_if_zero(form.application_id.data),
                actor_id=_actor(),
            ),
            back,
        )
        if done:
            return done
    return _form_page(form, f"Editar {repository.name}", back)


@bp.post("/repositorios/<int:entity_id>/activo")
@roles_required(*CATALOG_ROLES)
def repository_toggle(entity_id: int) -> Response:
    return _toggle(
        _get_or_404(Repository, entity_id),
        catalogs.set_repository_active,
        url_for("admin.repository_list"),
    )


def _simple_list(title: str, headers: list[str], rows: list[Any], prefix: str) -> str:
    return render_template(
        "admin/simple_list.html", title=title, headers=headers, rows=rows, prefix=prefix
    )


# --- Users and roles ---------------------------------------------------------------------------


@bp.get("/usuarios")
@roles_required(Role.ADMIN)
def user_list() -> str:
    return render_template("admin/user_list.html", users=auth.list_users(db.session))


@bp.route("/usuarios/nuevo", methods=["GET", "POST"])
@roles_required(Role.ADMIN)
def user_new() -> Response | str:
    form = forms.UserForm()
    back = url_for("admin.user_list")
    if form.validate_on_submit():
        roles = [Role(form.role.data)] if form.role.data else []
        done = _save(
            lambda: auth.create_user(
                db.session,
                email=_text(form.email),
                display_name=_text(form.display_name),
                password=_text(form.password),
                roles=roles,
                actor_id=_actor(),
            ),
            back,
        )
        if done:
            return done
    return _form_page(form, "Nuevo usuario", back)


@bp.get("/usuarios/<int:user_id>")
@roles_required(Role.ADMIN)
def user_detail(user_id: int) -> str:
    user = _get_or_404(User, user_id)
    return render_template(
        "admin/user_detail.html",
        user=user,
        form=forms.RoleForm(),
        labels=forms.ROLE_LABELS,
        roles=sorted(user.roles),
    )


@bp.post("/usuarios/<int:user_id>/roles")
@roles_required(Role.ADMIN)
def user_grant(user_id: int) -> Response:
    user = _get_or_404(User, user_id)
    form = forms.RoleForm()
    if form.validate_on_submit():
        auth.grant_role(db.session, user, Role(form.role.data), actor_id=_actor())
        flash("Rol asignado.", "info")
    return redirect(url_for("admin.user_detail", user_id=user.id))


@bp.post("/usuarios/<int:user_id>/roles/<role>/quitar")
@roles_required(Role.ADMIN)
def user_revoke(user_id: int, role: str) -> Response:
    user = _get_or_404(User, user_id)
    try:
        auth.revoke_role(db.session, user, Role(role), actor_id=_actor())
    except ValueError:
        abort(404)
    except auth.SelfLockoutError as err:
        db.session.rollback()
        _fail(err)
    else:
        flash("Rol retirado.", "info")
    return redirect(url_for("admin.user_detail", user_id=user.id))


@bp.post("/usuarios/<int:user_id>/activo")
@roles_required(Role.ADMIN)
def user_toggle(user_id: int) -> Response:
    user = _get_or_404(User, user_id)
    try:
        auth.set_user_active(db.session, user, not user.is_active, actor_id=_actor())
    except auth.SelfLockoutError as err:
        _fail(err)
    else:
        flash("Usuario activado." if user.is_active else "Usuario desactivado.", "info")
    return redirect(url_for("admin.user_detail", user_id=user.id))


# --- Policies ----------------------------------------------------------------------------------

POLICY_KINDS: dict[str, tuple[str, Any, Any, Any, Any]] = {
    "evaluacion": (
        "Política de evaluación",
        EvaluationPolicy,
        forms.EvaluationPolicyForm,
        policies.EvaluationPolicyData,
        policies.schedule_evaluation_policy,
    ),
    "trabajo": (
        "Política de jornada",
        WorkPolicy,
        forms.WorkPolicyForm,
        policies.WorkPolicyData,
        policies.schedule_work_policy,
    ),
}


@bp.get("/politicas")
@roles_required(Role.ADMIN)
def policy_list() -> str:
    today = _today()
    sections = []
    for slug, (title, model, _form, _data, _schedule) in POLICY_KINDS.items():
        versions = policies.list_policies(db.session, model)
        current = policies.policy_at(db.session, model, today)
        sections.append(
            {
                "slug": slug,
                "title": title,
                "versions": [
                    (v, _policy_values(v), v is current, v.effective_from > today) for v in versions
                ],
            }
        )
    return render_template("admin/policy_list.html", sections=sections)


def _policy_values(policy: Any) -> list[tuple[str, Any]]:
    form = next(f for _t, m, f, _d, _s in POLICY_KINDS.values() if isinstance(policy, m))()
    return [
        (field.label.text, getattr(policy, field.name))
        for field in form
        if field.name not in ("csrf_token", "effective_from")
    ]


@bp.route("/politicas/<slug>/nueva", methods=["GET", "POST"])
@roles_required(Role.ADMIN)
def policy_new(slug: str) -> Response | str:
    if slug not in POLICY_KINDS:
        abort(404)
    title, model, form_class, data_class, schedule = POLICY_KINDS[slug]
    form = form_class()
    back = url_for("admin.policy_list")
    if request.method == "GET":
        # Start from the policy in force so only the changes need typing.
        current = policies.policy_at(db.session, model, _today())
        if current is not None:
            form.process(obj=current)
            form.effective_from.data = None
    if form.validate_on_submit():
        values = {k: v for k, v in form.data.items() if k != "csrf_token"}
        done = _save(
            lambda: schedule(db.session, data_class(**values), today=_today(), actor_id=_actor()),
            back,
        )
        if done:
            return done
    return _form_page(form, f"Nueva versión · {title}", back)
