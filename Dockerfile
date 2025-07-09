
# Base image with Python
FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies including Ollama
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    curl \
    gnupg \
    libssl-dev \
    libffi-dev \
    && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/10/prod.list > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y \
    msodbcsql17 \
    unixodbc \
    unixodbc-dev \
    && curl -fsSL https://ollama.com/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt fastapi "uvicorn[standard]" PyJWT

# Copy application code
COPY . .

# Expose FastAPI port
EXPOSE 8000

# Create startup script
RUN echo '#!/bin/bash\n\
ollama serve &\n\
sleep 10\n\
ollama pull llama3.2\n\
uvicorn main:app --host 0.0.0.0 --port 8000' > /app/start.sh && chmod +x /app/start.sh

# Start both Ollama and FastAPI
CMD ["/app/start.sh"]
