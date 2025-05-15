# Use Python 3.10 as the base image
FROM python:3.10-slim

# Set environment variables to prevent interactive prompts during installation
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies required by the application
# - Tesseract for OCR
# - Poppler for pdf2image
# - wkhtmltopdf for pdfkit (HTML to PDF)
# - libgl1-mesa-glx often needed for GUI-less image processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    wget \
    libgl1-mesa-glx \
    build-essential \
    python3-dev \
    libpoppler-cpp-dev \
    libtesseract-dev \
    xfonts-75dpi \
    xfonts-base \
    wkhtmltopdf \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*    watch -n 1 df -h

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY app.py .
# If you have other .py files or assets, copy them too:
# COPY ./helpers /app/helpers

# Make port 5002 available to the world outside this container
EXPOSE 5002

# Ensure it's writable by the user running the app inside the container.
RUN mkdir -p /app/local_outputs && chmod 777 /app/local_outputs

# --- Use runtime environment variables for sensitive data ---
# These should be passed at runtime using `docker run -e` or a `.env` file.
ENV PORT=docker build -t pdf-processor-api .2
ENV LOCAL_OUTPUT_DIR="/app/local_outputs"
ENV GEMINI_API_KEY="AIzaSyDmip6WwHwNmDekPTwCo8FK1nnc24Ifhv4"
ENV S3_BUCKET_NAME="exchange-mern-webapp-bucket"
ENV AWS_ACCESS_KEY_ID="AKIAZQHXXJFS7HGCYI6R"
ENV AWS_SECRET_ACCESS_KEY="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"
ENV AWS_REGION="ap-south-1" 
ENV LOCAL_OUTPUT_DIR="/app/local_outputs"


# Run the application using Gunicorn for production
CMD ["gunicorn", "--bind", "0.0.0.0:5002", "--workers", "2", "--threads", "4", "--timeout", "120", "app:app"]