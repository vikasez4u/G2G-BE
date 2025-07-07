# Base image with Python
FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies

RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    curl \
    gnupg && \
    curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - && \
    curl https://packages.microsoft.com/config/debian/10/prod.list > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y \
    msodbcsql17\
    unixodbc \
    unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*


# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip && pip install -r requirements.txt  fastapi "uvicorn[standard]"

# Copy application code
COPY . .

# Expose FastAPI port
EXPOSE 8000

# Run FastAPI app
#CMD ["sleep", "infinity"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]


