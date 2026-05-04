# Use a slim Python 3.9 base image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the simulator runtime and package modules into the container
COPY simulator.py .
COPY simulator/ ./simulator/

EXPOSE 9102

CMD ["python", "simulator.py"]
