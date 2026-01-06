# 🐍 Base image with Python
FROM python:3.13-slim

# 🔧 Environment settings
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# 📁 Working directory
WORKDIR /app

# 📦 Clean and update apt cache and install minimal system dependencies
RUN apt-get update --fix-missing \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       curl \
       gnupg \
       lsb-release \
       build-essential \
       git \
       libssl-dev \
       libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# 🏢 Microsoft SQL packages setup (no apt-key)
# - write Microsoft GPG key into trusted keyring
# - add the Microsoft prod.list
# - update and install msodbcsql17, unixodbc, unixodbc-dev
RUN set -eux; \
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /etc/apt/trusted.gpg.d/microsoft.gpg; \
    curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list -o /etc/apt/sources.list.d/mssql-release.list || \
      curl -fsSL https://packages.microsoft.com/config/debian/11/prod.list -o /etc/apt/sources.list.d/mssql-release.list; \
    apt-get update --fix-missing; \
    ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql17 unixodbc unixodbc-dev; \
    rm -rf /var/lib/apt/lists/*

# 🤖 Ollama installation
RUN curl -fsSL https://ollama.com/install.sh | sh

# 📂 Copy requirements file
COPY requirements.txt .

# 🐍 Install Python dependencies
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    fastapi \
    "uvicorn[standard]" \
    PyJWT

# 📂 Copy app code
COPY . .

# 🌐 Expose FastAPI port and Ollama ports
EXPOSE 8000 11434

# 🚀 Startup script for Ollama & FastAPI
RUN echo '#!/bin/bash\n\
ollama serve &\n\
sleep 10\n\
ollama pull llama3.2\n\
uvicorn main:app --host 0.0.0.0 --port 8000' > /app/start.sh \
    && chmod +x /app/start.sh

# 📌 Default command
CMD ["/app/start.sh"]