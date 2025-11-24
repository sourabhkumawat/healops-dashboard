#!/bin/bash
# HealOps Universal Agent Installer
# Supports: Linux (Ubuntu, CentOS, Debian), macOS

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
HEALOPS_ENDPOINT="${HEALOPS_ENDPOINT:-https://api.healops.com}"
HEALOPS_API_KEY="${HEALOPS_API_KEY:-}"
AGENT_VERSION="1.0.0"
INSTALL_DIR="/opt/healops"
CONFIG_DIR="/etc/healops"
LOG_DIR="/var/log/healops"

echo -e "${GREEN}🚀 HealOps Universal Agent Installer v${AGENT_VERSION}${NC}"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
   echo -e "${RED}❌ Please run as root (use sudo)${NC}"
   exit 1
fi

# Detect OS
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        VER=$VERSION_ID
    elif [ -f /etc/redhat-release ]; then
        OS="rhel"
    elif [ "$(uname)" == "Darwin" ]; then
        OS="macos"
    else
        OS="unknown"
    fi
    
    echo -e "${GREEN}✓ Detected OS: $OS${NC}"
}

# Create directories
setup_directories() {
    echo "📁 Creating directories..."
    mkdir -p $INSTALL_DIR
    mkdir -p $CONFIG_DIR
    mkdir -p $LOG_DIR
    echo -e "${GREEN}✓ Directories created${NC}"
}

# Download agent binary
download_agent() {
    echo "📥 Downloading HealOps agent..."
    
    # Determine architecture
    ARCH=$(uname -m)
    if [ "$ARCH" == "x86_64" ]; then
        ARCH="amd64"
    elif [ "$ARCH" == "aarch64" ]; then
        ARCH="arm64"
    fi
    
    # Download URL (replace with actual release URL)
    DOWNLOAD_URL="https://releases.healops.com/agent/${AGENT_VERSION}/healops-agent-${OS}-${ARCH}"
    
    # For now, create a placeholder script
    cat > ${INSTALL_DIR}/healops-agent << 'EOF'
#!/usr/bin/env python3
"""
HealOps Agent - Collects and forwards logs to HealOps platform
"""
import os
import sys
import time
import json
import requests
import subprocess
from datetime import datetime
from pathlib import Path

class HealOpsAgent:
    def __init__(self, config_file):
        self.config = self.load_config(config_file)
        self.endpoint = self.config.get('endpoint', 'https://api.healops.com')
        self.api_key = self.config.get('api_key', '')
        self.log_sources = self.config.get('log_sources', ['/var/log/syslog', '/var/log/messages'])
        
    def load_config(self, config_file):
        """Load configuration from file."""
        try:
            with open(config_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}")
            return {}
    
    def tail_logs(self, log_file):
        """Tail a log file and yield new lines."""
        try:
            # Use tail -F to follow log file
            process = subprocess.Popen(
                ['tail', '-F', '-n', '0', log_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            for line in iter(process.stdout.readline, ''):
                if line:
                    yield line.strip()
                    
        except Exception as e:
            print(f"Error tailing {log_file}: {e}")
    
    def send_log(self, log_entry):
        """Send log entry to HealOps."""
        try:
            payload = {
                'service_name': 'system',
                'level': self.detect_level(log_entry),
                'message': log_entry,
                'timestamp': datetime.utcnow().isoformat(),
                'metadata': {
                    'source': 'healops-agent',
                    'hostname': os.uname().nodename
                }
            }
            
            response = requests.post(
                f"{self.endpoint}/ingest/logs",
                json=payload,
                headers={'X-API-Key': self.api_key},
                timeout=5
            )
            
            if response.status_code == 200:
                print(f"✓ Log sent: {log_entry[:50]}...")
            else:
                print(f"✗ Failed to send log: {response.status_code}")
                
        except Exception as e:
            print(f"Error sending log: {e}")
    
    def detect_level(self, message):
        """Detect log level from message."""
        message_lower = message.lower()
        if 'error' in message_lower or 'fail' in message_lower:
            return 'ERROR'
        elif 'warn' in message_lower:
            return 'WARN'
        elif 'crit' in message_lower or 'fatal' in message_lower:
            return 'CRITICAL'
        else:
            return 'INFO'
    
    def run(self):
        """Main agent loop."""
        print(f"🚀 HealOps Agent started")
        print(f"📡 Endpoint: {self.endpoint}")
        print(f"📂 Monitoring: {', '.join(self.log_sources)}")
        
        # Monitor all log sources
        import threading
        
        def monitor_file(log_file):
            for line in self.tail_logs(log_file):
                self.send_log(line)
        
        threads = []
        for log_file in self.log_sources:
            if Path(log_file).exists():
                thread = threading.Thread(target=monitor_file, args=(log_file,))
                thread.daemon = True
                thread.start()
                threads.append(thread)
        
        # Keep running
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n👋 HealOps Agent stopped")

if __name__ == '__main__':
    config_file = '/etc/healops/config.json'
    agent = HealOpsAgent(config_file)
    agent.run()
EOF
    
    chmod +x ${INSTALL_DIR}/healops-agent
    echo -e "${GREEN}✓ Agent installed${NC}"
}

# Create configuration
create_config() {
    echo "⚙️  Creating configuration..."
    
    # Prompt for API key if not provided
    if [ -z "$HEALOPS_API_KEY" ]; then
        echo -e "${YELLOW}Please enter your HealOps API key:${NC}"
        read -r HEALOPS_API_KEY
    fi
    
    cat > ${CONFIG_DIR}/config.json << EOF
{
  "endpoint": "${HEALOPS_ENDPOINT}",
  "api_key": "${HEALOPS_API_KEY}",
  "log_sources": [
    "/var/log/syslog",
    "/var/log/messages",
    "/var/log/auth.log"
  ],
  "heartbeat_interval": 60
}
EOF
    
    chmod 600 ${CONFIG_DIR}/config.json
    echo -e "${GREEN}✓ Configuration created${NC}"
}

# Create systemd service (Linux)
create_systemd_service() {
    echo "🔧 Creating systemd service..."
    
    cat > /etc/systemd/system/healops-agent.service << EOF
[Unit]
Description=HealOps Agent
After=network.target

[Service]
Type=simple
User=root
ExecStart=${INSTALL_DIR}/healops-agent
Restart=always
RestartSec=10
StandardOutput=append:${LOG_DIR}/agent.log
StandardError=append:${LOG_DIR}/agent.log

[Install]
WantedBy=multi-user.target
EOF
    
    systemctl daemon-reload
    systemctl enable healops-agent
    systemctl start healops-agent
    
    echo -e "${GREEN}✓ Service created and started${NC}"
}

# Create launchd service (macOS)
create_launchd_service() {
    echo "🔧 Creating launchd service..."
    
    cat > /Library/LaunchDaemons/com.healops.agent.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.healops.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>${INSTALL_DIR}/healops-agent</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/agent.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/agent.log</string>
</dict>
</plist>
EOF
    
    launchctl load /Library/LaunchDaemons/com.healops.agent.plist
    
    echo -e "${GREEN}✓ Service created and started${NC}"
}

# Main installation flow
main() {
    detect_os
    setup_directories
    download_agent
    create_config
    
    if [ "$OS" == "macos" ]; then
        create_launchd_service
    else
        create_systemd_service
    fi
    
    echo ""
    echo -e "${GREEN}✅ HealOps Agent installed successfully!${NC}"
    echo ""
    echo "📊 Check status:"
    if [ "$OS" == "macos" ]; then
        echo "   launchctl list | grep healops"
    else
        echo "   systemctl status healops-agent"
    fi
    echo ""
    echo "📝 View logs:"
    echo "   tail -f ${LOG_DIR}/agent.log"
    echo ""
    echo "🔧 Configuration:"
    echo "   ${CONFIG_DIR}/config.json"
    echo ""
}

# Run installation
main
