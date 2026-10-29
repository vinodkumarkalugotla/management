FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# SQLite file lives in /data - mount a volume there to keep it, or set DATABASE_URL to Postgres.
ENV DATABASE_URL=sqlite:////data/hrms.db
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8000"]
