# 🐍 Base image with Python
FROM python:3.13-slim

# 🔧 Environment settings
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 📁 Working directory
WORKDIR /app

# 📦 Clean and update apt cache
RUN apt-get clean && rm -rf /var/lib/apt/lists/* \
    && apt-get update --fix-missing

# 🛠 Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    curl \
    gnupg \
    apt-transport-https \
    libssl-dev \
    libffi-dev

# 🏢 Microsoft SQL packages setup (Debian 11 / bullseye)
RUN curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /etc/apt/trusted.gpg.d/microsoft.gpg \
    && curl https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y \
        msodbcsql17 \
        unixodbc \
        unixodbc-dev


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
