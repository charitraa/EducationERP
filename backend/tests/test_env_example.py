"""`.env.example` must document exactly the variables the settings read.

Config drift is silent: a setting gains an env var, nobody adds it to the
example, and the next person to deploy gets a default they never chose. This
walks the settings modules and compares them to the file.
"""
import ast
import pathlib

from django.test import SimpleTestCase

BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent
SETTINGS_DIR = BACKEND_DIR / "config" / "settings"
ENV_EXAMPLE = BACKEND_DIR / ".env.example"


def settings_variables() -> set[str]:
    """Every name passed to `config("...")` anywhere in config/settings/."""
    names = set()
    for path in SETTINGS_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "config"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                names.add(node.args[0].value)
    return names


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    """Mirror python-decouple's RepositoryEnv parser, quirks included."""
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


class EnvExampleTests(SimpleTestCase):
    def test_every_setting_variable_is_documented(self):
        missing = settings_variables() - set(parse_env_file(ENV_EXAMPLE))

        self.assertFalse(
            missing,
            f"Read by config/settings/ but absent from .env.example: {sorted(missing)}",
        )

    def test_no_undocumented_leftovers(self):
        extra = set(parse_env_file(ENV_EXAMPLE)) - settings_variables()

        self.assertFalse(
            extra,
            f"In .env.example but no longer read by any setting: {sorted(extra)}",
        )

    def test_values_do_not_carry_inline_comments(self):
        # decouple does not strip a trailing "#" comment — it becomes part of
        # the value, so `DB_PORT=5432  # default` yields the string
        # "5432  # default" and the int cast blows up at startup.
        offenders = [
            key for key, value in parse_env_file(ENV_EXAMPLE).items() if "#" in value
        ]

        self.assertFalse(offenders, f"Inline comment in value for: {offenders}")

    def test_example_declares_no_real_secret(self):
        values = parse_env_file(ENV_EXAMPLE)

        self.assertFalse(values["DB_PASSWORD"])
        self.assertFalse(values["EMAIL_HOST_PASSWORD"])
        self.assertIn("change-me", values["SECRET_KEY"])
