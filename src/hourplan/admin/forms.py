from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    DecimalField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
)
from wtforms.validators import URL, DataRequired, InputRequired, Length, NumberRange, Optional

from hourplan.auth.roles import Role
from hourplan.shared.i18n import N_

REQUIRED = DataRequired(message="Este campo es obligatorio.")
CODE = [REQUIRED, Length(max=40)]
NAME = [REQUIRED, Length(max=120)]
ORDER = IntegerField("Orden", default=0, validators=[Optional(), NumberRange(-9999, 9999)])

ROLE_LABELS = {
    Role.ADMIN: N_("Administración"),
    Role.PM: N_("Gestión de proyectos"),
    Role.LEAD: N_("Líder técnico"),
    Role.DEVELOPER: N_("Desarrollo"),
    Role.OBSERVER: N_("Solo lectura"),
}


class CatalogItemForm(FlaskForm):
    code = StringField("Código", validators=CODE)
    name = StringField("Nombre", validators=NAME)
    parent_id = SelectField("Pertenece a", coerce=int, validate_choice=False, default=0)
    sort_order = ORDER
    is_closed = BooleanField("Estado de cierre")
    attributable_to_developer = BooleanField("Atribuible al desarrollador", default=True)
    is_contingency = BooleanField("Es contingencia")


class PortfolioForm(FlaskForm):
    code = StringField("Código", validators=CODE)
    name = StringField("Nombre", validators=NAME)
    sort_order = ORDER


class ApplicationForm(FlaskForm):
    code = StringField("Código", validators=CODE)
    name = StringField("Nombre", validators=NAME)
    type_id = SelectField("Tipo", coerce=int, validate_choice=False, default=0)


class RepositoryForm(FlaskForm):
    name = StringField("Nombre", validators=[REQUIRED, Length(max=200)])
    url = StringField(
        "URL",
        validators=[Optional(), Length(max=500), URL(message="Escribe una URL válida.")],
    )
    application_id = SelectField("Aplicación", coerce=int, validate_choice=False, default=0)


class UserForm(FlaskForm):
    email = StringField("Correo", validators=[REQUIRED, Length(max=254)])
    display_name = StringField("Nombre visible", validators=NAME)
    password = PasswordField(
        "Contraseña inicial",
        validators=[REQUIRED, Length(min=12, max=1024, message="Mínimo 12 caracteres.")],
        render_kw={"autocomplete": "new-password"},
    )
    role = SelectField(
        "Rol inicial",
        choices=[("", N_("Sin rol"))] + [(r.value, ROLE_LABELS[r]) for r in Role],
        default="",
    )


class RoleForm(FlaskForm):
    role = SelectField("Rol", choices=[(r.value, ROLE_LABELS[r]) for r in Role])


class ResourceLinkForm(FlaskForm):
    resource_id = SelectField("Recurso existente", coerce=int, validate_choice=False, default=0)
    name = StringField("o nombre del recurso nuevo", validators=[Optional(), Length(max=120)])


class _PolicyForm(FlaskForm):
    effective_from = DateField("Vigente desde", validators=[InputRequired("Elige una fecha.")])


def _pct(label: str) -> DecimalField:
    return DecimalField(label, places=2, validators=[InputRequired(), NumberRange(0, 999)])


class EvaluationPolicyForm(_PolicyForm):
    colors_enabled = BooleanField("Usar colores", default=True)
    hours_tolerance_pct = _pct(N_("Tolerancia de horas (%)"))
    hours_red_pct = _pct(N_("Límite rojo de horas (%)"))
    dates_tolerance_pct = _pct(N_("Tolerancia de fechas (%)"))
    dates_red_pct = _pct(N_("Límite rojo de fechas (%)"))


class WorkPolicyForm(_PolicyForm):
    standard_daily_hours = DecimalField(
        "Horas diarias estándar", places=2, validators=[InputRequired(), NumberRange(0.01, 24)]
    )
    daily_warning_hours = DecimalField(
        "Aviso sobre horas diarias", places=2, validators=[InputRequired(), NumberRange(0.01, 24)]
    )
    max_project_weeks = IntegerField(
        "Semanas máximas por proyecto", validators=[InputRequired(), NumberRange(1, 52)]
    )
    idle_minutes = IntegerField(
        "Minutos de inactividad", validators=[InputRequired(), NumberRange(1, 240)]
    )
