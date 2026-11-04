from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField
from wtforms.validators import DataRequired, Length


class LoginForm(FlaskForm):
    email = StringField(
        "Correo",
        validators=[DataRequired(message="Escribe tu correo."), Length(max=254)],
        render_kw={"autocomplete": "username", "inputmode": "email"},
    )
    password = PasswordField(
        "Contraseña",
        validators=[DataRequired(message="Escribe tu contraseña."), Length(max=1024)],
        render_kw={"autocomplete": "current-password"},
    )
    remember = BooleanField("Mantener la sesión iniciada")
