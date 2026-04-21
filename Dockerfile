# Use a slim Python 3.9 base image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the simulator script into the container
COPY simulator.py .

# Command to run the simulator
CMD ["python", "simulator.py"]
