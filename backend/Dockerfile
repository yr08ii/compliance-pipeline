FROM python:3.13-slim
RUN pip install uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .
CMD ["uv", "run", "uvicorn", "compliance.api:app", "--host", "0.0.0.0", "--port", "8000"]
