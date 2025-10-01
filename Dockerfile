FROM ubuntu:22.04
# Prevent interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
# Install system dependencies
RUN apt-get update && apt-get install -y \
    # Add repository for python3.12
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y \
    # python3.12 and pip
    python3.12 \
    python3.12-dev \
    python3.12-venv \
    python3-pip \
    build-essential \
    # Browser dependencies
    wget \
    curl \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    xdg-utils \
    # X11 and VNC dependencies
    xvfb \
    x11vnc \
    fluxbox \
    novnc \
    websockify \
    # Additional utilities
    net-tools \
    vim \
    && rm -rf /var/lib/apt/lists/*
    
# Install Node.js (latest LTS version)
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*
   
# Install pip for python3.12
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12
# Set python3.12 as default python3
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1
# Set up working directory
WORKDIR /app
# Copy Python requirements and install
COPY requirements.txt .
# Upgrade pip and setuptools first
RUN python3.12 -m pip install --upgrade pip setuptools wheel
# Install supervisor
RUN python3.12 -m pip install supervisor
# Install requirements
RUN python3.12 -m pip install --no-cache-dir -r requirements.txt

# Prepare persistent browser profile directory and seed cookies
RUN mkdir -p /app/browser-profiles/test-profile-3

COPY cookies.sqlite /app/browser-profiles/test-profile-3/cookies.sqlite
# Copy application files
COPY . .
# Create necessary directories
RUN mkdir -p /var/log/supervisor \
    /root/.vnc \
    /app/static
# Set up VNC password
RUN x11vnc -storepasswd vncpassword /root/.vnc/passwd
# Copy supervisor configuration
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
# Copy the split-screen HTML interface
COPY split-interface.html /app/static/index.html
# Create startup script
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh
# Expose ports
EXPOSE 5000 5900 6080
# Start supervisor
CMD ["/app/docker-entrypoint.sh"]