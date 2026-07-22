.PHONY: db backend-migrate seed-run frontend serve

db:
	docker compose up -d db

backend-migrate:
	cd backend && DATABASE_URL=$${DATABASE_URL:-postgresql+psycopg://compliance:compliance@localhost:5432/compliance} uv run alembic upgrade head

seed-run:
	cd backend && uv run compliance-run

frontend:
	cd frontend && npm run build

serve:
	cd backend && FRONTEND_DIST=../frontend/dist uv run uvicorn compliance.api:app --port 8000