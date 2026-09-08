# 1. Start with a lightweight version of Python
FROM python:3.10-slim

# 2. Create a working folder inside the container
WORKDIR /app

# 3. Copy our grocery list into the container
COPY requirements.txt .

# 4. Install the tools from the grocery list
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy all our actual code into the container
COPY . .

# 6. Expose the port so the internet can talk to it
EXPOSE 8080

# 7. The command to turn the server on!
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]