FROM python:3.11-slim
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install dependencies directly to the Docker system using uv
RUN pip install uv
RUN uv pip install --system -r requirements.txt
# Copy application code
COPY server.py .
COPY client-sse.py .

# Expose the port the server runs on
EXPOSE 8050

# Command to run the server natively
CMD ["python", "server.py"]