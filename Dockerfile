# Use the official Microsoft Azure Functions Python 3.11 base image
FROM mcr.microsoft.com/azure-functions/python:4-python3.11

# Set the Azure Functions script root directory
ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true

# Set Playwright browser path to a globally accessible directory
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Copy requirements.txt and install Python dependencies
COPY requirements.txt /
RUN pip install --no-cache-dir -r /requirements.txt

# Install Playwright browser binaries and system-level dependencies for Chromium
RUN mkdir -p /ms-playwright && \
    apt-get update && \
    playwright install chromium && \
    playwright install-deps chromium && \
    chmod -R 777 /ms-playwright && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy the application code into the Azure Functions root directory
COPY . /home/site/wwwroot
