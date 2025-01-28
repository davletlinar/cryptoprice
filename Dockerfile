# Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY main.py .

# prevent Python from buffering output
ENV PYTHONUNBUFFERED=1

# Run the application
CMD ["python3", "main.py"]