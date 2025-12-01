FROM python:3.9-slim

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy code
COPY . .

# Set Default Envs
ENV VECTOR_HOST=0.0.0.0
ENV VECTOR_PORT=8000
ENV VECTOR_DB_PATH=/data/chroma
ENV VECTOR_MODEL=all-MiniLM-L6-v2

# Volume for persistence
VOLUME /data/chroma

# Expose port
EXPOSE 8000

# Start
CMD ["python", "vector_service_api.py"]
