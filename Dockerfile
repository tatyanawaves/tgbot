FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# SQLite будет храниться в /data (persistent volume на Fly.io)
ENV DATA_DIR=/data

# Run the bot
CMD ["python", "bot.py"]
