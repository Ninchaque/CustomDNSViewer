# Custom DNS Viewer

A modern, web-based DNS BIND zone management interface built with Flask. This application provides an intuitive way to view, manage, and edit DNS records for BIND DNS servers with SSH connectivity and automatic zone discovery.

## 🚀 Features

### Core Functionality
- **DNS Zone Visualization**: Browse and display DNS records by zone and type
- **Automatic Zone Discovery**: Intelligently discovers zones from BIND configuration and zone files
- **Record Management**: Add, edit, and delete DNS records with real-time validation
- **Multiple Record Types**: Support for A, AAAA, CNAME, MX, NS, PTR, TXT, SRV records
- **SSH Integration**: Secure remote management via SSH with paramiko
- **YAML Configuration**: Flexible configuration through `zones_config.yaml`

### Advanced Features
- **Smart Zone Detection**: Scans `/etc/bind/zone/direct/` and `/etc/bind/zone/reverse/` directories
- **Automatic Backups**: Creates timestamped backups in `/etc/bind/backup/` before modifications
- **DNS Walking**: Advanced subdomain discovery and DNS reconnaissance
- **Real-time Validation**: Zone file syntax validation with named-checkzone
- **Responsive UI**: Modern, mobile-friendly interface with FontAwesome icons
- **Fast Queries**: Optimized DNS timeouts for quick responses

## 📋 Prerequisites

- Python 3.7+
- Access to a BIND DNS server via SSH
- BIND server with `named-checkzone` and `rndc` available
- Network connectivity to the DNS server (default: 192.168.1.201)

## 🛠️ Installation

### Local Development Setup

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd CustomDNSViewer
   ```

2. **Create virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure the application** (see Configuration section below)

5. **Run the application**:
   ```bash
   python app.py
   ```

6. **Access the interface**:
   Open your browser to `http://localhost:8080`

## ⚙️ Configuration

### zones_config.yaml

The application uses a YAML configuration file to manage zones and discovery settings:

```yaml
# Fallback zones used when automatic discovery fails
fallback_zones:
  - localhost
  - solal.internal
  - 127.in-addr.arpa
  - local

# Test zones for DNS discovery (when SSH is not available)
test_zones:
  - localhost
  - 127.in-addr.arpa
  - local
  - solal.internal
  - 1.168.192.in-addr.arpa

# System zones to ignore during automatic discovery
system_zones:
  - localhost
  - 127.in-addr.arpa
  - 0.in-addr.arpa
  - 255.in-addr.arpa
  - root.hint
  - hint
  - "."
  - cache
  - bind
  - version.bind
  - hostname.bind

# DNS discovery configuration
discovery:
  # Maximum number of subdomains to test
  max_subdomains: 50
  
  # Timeout for DNS queries in seconds (reduced for faster responses)
  dns_timeout: 1
  
  # Enable/disable subdomain discovery
  enable_subdomain_discovery: true
  
  # Enable/disable DNS walking
  enable_dns_walking: true
```

### Configuration Options

| Setting | Description | Default |
|---------|-------------|---------|
| `fallback_zones` | Zones used when automatic discovery fails | `[localhost, solal.internal, ...]` |
| `test_zones` | Zones tested for DNS connectivity | `[localhost, 127.in-addr.arpa, ...]` |
| `system_zones` | Zones ignored during discovery | `[localhost, bind, ...]` |
| `max_subdomains` | Maximum subdomains to test during discovery | `50` |
| `dns_timeout` | DNS query timeout in seconds | `1` |
| `enable_subdomain_discovery` | Enable/disable subdomain scanning | `true` |
| `enable_dns_walking` | Enable/disable DNS walking techniques | `true` |

## 🔒 SSH Configuration

The application requires SSH access to manage BIND zone files:

1. **Navigate to SSH Configuration** in the web interface
2. **Enter your SSH credentials**:
   - **Hostname**: DNS server IP (e.g., 192.168.1.201)
   - **Username**: SSH username with BIND file access
   - **Password**: SSH password
   - **Port**: SSH port (default: 22)
   - **Zone Files Path**: Base zone directory (default: /etc/bind/zone)

3. **Test the connection** before saving

### Required Permissions

The SSH user must have:
- Read access to `/etc/bind/zone/direct/` and `/etc/bind/zone/reverse/`
- Write access to zone files for modifications
- Write access to `/etc/bind/backup/` for backups
- Execute access to `named-checkzone` and `rndc`

## 🏗️ BIND Server Structure

The application expects the following directory structure on your BIND server:

```
/etc/bind/
├── zone/
│   ├── direct/          # Forward DNS zones
│   │   ├── db.example.com
│   │   ├── db.solal.internal
│   │   └── ...
│   └── reverse/         # Reverse DNS zones
│       ├── db.1.168.192.in-addr.arpa
│       ├── db.2001:db8::.ip6.arpa
│       └── ...
├── backup/              # Automatic backups (created automatically)
│   ├── db.example.com.backup.20231201_143022
│   └── ...
└── named.conf.local     # BIND configuration (optional scanning)
```

## 🚢 Deployment Options

### 1. Local Development

Perfect for development and testing:

```bash
python app.py
```

Access at: `http://localhost:8080`

### 2. Docker Deployment

#### Build and Run

```bash
# Build the Docker image
docker build -t custom-dns-viewer .

# Run the container
docker run -d \
  --name dns-viewer \
  -p 8080:8080 \
  custom-dns-viewer
```

#### Docker Compose

```yaml
version: '3.8'
services:
  dns-viewer:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./zones_config.yaml:/app/zones_config.yaml
    restart: unless-stopped
```

Run with: `docker-compose up -d`

### 3. Kubernetes (K3s) Deployment

#### Deployment Manifest

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: custom-dns-viewer
  namespace: dns-management
spec:
  replicas: 1
  selector:
    matchLabels:
      app: custom-dns-viewer
  template:
    metadata:
      labels:
        app: custom-dns-viewer
    spec:
      containers:
      - name: dns-viewer
        image: custom-dns-viewer:latest
        ports:
        - containerPort: 8080
        volumeMounts:
        - name: config
          mountPath: /app/zones_config.yaml
          subPath: zones_config.yaml
      volumes:
      - name: config
        configMap:
          name: dns-viewer-config
---
apiVersion: v1
kind: Service
metadata:
  name: custom-dns-viewer-service
spec:
  selector:
    app: custom-dns-viewer
  ports:
  - port: 80
    targetPort: 8080
  type: ClusterIP
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: custom-dns-viewer-ingress
  annotations:
    kubernetes.io/ingress.class: traefik
spec:
  rules:
  - host: dns.solal.internal
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: custom-dns-viewer-service
            port:
              number: 80
```

#### Deploy to K3s

```bash
# Create namespace
kubectl create namespace dns-management

# Create ConfigMap for configuration
kubectl create configmap dns-viewer-config \
  --from-file=zones_config.yaml \
  -n dns-management

# Apply deployment
kubectl apply -f k8s-deployment.yaml -n dns-management
```

Access at: `https://dns.solal.internal`

### 4. Production Deployment

For production environments, consider:

#### Using a Production WSGI Server

```bash
# Install Gunicorn
pip install gunicorn

# Run with Gunicorn
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

#### Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name dns.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

#### Systemd Service

```ini
[Unit]
Description=Custom DNS Viewer
After=network.target

[Service]
Type=simple
User=dnsviewer
WorkingDirectory=/opt/CustomDNSViewer
ExecStart=/opt/CustomDNSViewer/venv/bin/gunicorn -w 4 -b 127.0.0.1:8080 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

## 📖 Usage

### Viewing DNS Records

1. **Select a zone** from the dropdown menu
2. **Choose record type** filter (All, Direct, Reverse, Special)
3. **Click "Load Records"** to display results
4. **Use the refresh button** (🔄) to discover new zones

### Adding Records

1. **Click "Add Record"** button
2. **Fill in the form**:
   - Zone: Select target zone
   - Name: Record name (leave empty for zone root)
   - Type: Choose record type
   - Value: Record value (validated by type)
   - TTL: Time to live (60-86400 seconds)
3. **Submit** - automatically redirects to view the new record

### Editing Records

1. **Click the edit icon** (✏️) next to any record
2. **Modify values** in the modal dialog
3. **Save changes** - updates zone file and reloads DNS

### Deleting Records

1. **Click the delete icon** (🗑️) next to any record
2. **Confirm deletion** in the dialog
3. **Record is removed** from zone file and DNS reloaded

## 🔧 Troubleshooting

### Common Issues

1. **SSH Connection Failed**
   - Verify credentials and network connectivity
   - Check SSH service is running on target server
   - Ensure user has proper permissions

2. **Zone Discovery Issues**
   - Verify zone file structure matches expected layout
   - Check SSH user can read zone directories
   - Review `zones_config.yaml` fallback settings

3. **DNS Query Timeouts**
   - Adjust `dns_timeout` in configuration
   - Check network connectivity to DNS server
   - Verify DNS server is responding

4. **Zone File Validation Errors**
   - Ensure `named-checkzone` is installed
   - Check zone file syntax manually
   - Verify proper SOA record format

### Debug Mode

Enable debug logging by setting Flask debug mode:

```python
# In app.py
app.run(debug=True, host='0.0.0.0', port=8080)
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 👥 Support

For support and questions:
- Open an issue on GitHub
- Check the troubleshooting section
- Review configuration examples

---

**Custom DNS Viewer** - Simplifying BIND DNS management through modern web interfaces.
