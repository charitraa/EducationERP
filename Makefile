# Thin wrappers over docker compose.
#
# Compose interpolates ${VAR} in the compose files from the *project root*
# .env, but this project keeps its configuration in backend/.env — hence the
# --env-file on every target. Running the raw commands works too, as long as
# that flag comes along.

COMPOSE := docker compose --env-file ./backend/.env
DEV     := $(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: build up dev down logs errors shell bash migrate superuser seed test ps clean

build:           ## Build the backend image
	$(COMPOSE) build

up:              ## Start the production-shaped stack (gunicorn)
	$(COMPOSE) up -d --build

dev:             ## Start the development stack (runserver, hot reload)
	$(DEV) up --build

down:            ## Stop everything, keep volumes
	$(COMPOSE) down

logs:            ## Tail the backend logs
	$(COMPOSE) logs -f backend

errors:          ## Tail the 500 traceback log from inside the container
	$(COMPOSE) exec backend tail -f /app/logs/errors.log

shell:           ## Django shell in the running container
	$(COMPOSE) exec backend python manage.py shell

bash:            ## A shell in the running container
	$(COMPOSE) exec backend sh

migrate:         ## Apply migrations
	$(COMPOSE) exec backend python manage.py migrate

# Dev stack only: seed_demo refuses to run with DEBUG off, which is what the
# production-shaped stack runs with.
seed:            ## Load demo organizations, campuses and users
	$(COMPOSE) exec backend python manage.py seed_demo

superuser:       ## Create an admin user
	$(COMPOSE) exec backend python manage.py createsuperuser

# The compose file pins DJANGO_SETTINGS_MODULE to production, which beats
# manage.py's "test -> config.settings.test" default, so it is overridden here.
# Test settings use in-memory SQLite: no migrations or static pass needed.
test:            ## Run the suite inside the container
	$(COMPOSE) run --rm --no-deps \
		-e DJANGO_SETTINGS_MODULE=config.settings.test \
		-e DB_HOST= -e RUN_MIGRATIONS=false -e RUN_COLLECTSTATIC=false \
		backend python manage.py test

ps:              ## Show service status
	$(COMPOSE) ps

clean:           ## Stop and delete volumes — DESTROYS the database
	$(COMPOSE) down -v
