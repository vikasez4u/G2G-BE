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


