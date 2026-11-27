.PHONY: up down migrate revision test logs-worker prod-up prod-down prod-migrate prod-logs

up:
	docker compose up --build

down:
	docker compose down

migrate:
	docker compose exec backend alembic upgrade head

revision:
	docker compose exec backend alembic revision --autogenerate -m "$(m)"

test:
	docker compose exec backend pytest -v

logs-worker:
	docker compose logs -f worker

prod-up:
	docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build

prod-down:
	docker compose --env-file .env.prod -f docker-compose.prod.yml down

prod-migrate:
	docker compose --env-file .env.prod -f docker-compose.prod.yml run --rm migrate

prod-logs:
	docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f caddy backend worker frontend
