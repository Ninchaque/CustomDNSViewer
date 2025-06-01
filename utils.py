import dns.resolver
import dns.zone
import dns.query
import dns.rdatatype
import re
import logging
import os
import tempfile
import time
from typing import List, Dict, Any

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False
    paramiko = None

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    yaml = None

logger = logging.getLogger(__name__)


class DNSManager:
    """DNS BIND operations manager"""

    def __init__(self, dns_server: str):
        self.dns_server = dns_server
        self.resolver = dns.resolver.Resolver()
        self.resolver.nameservers = [dns_server]
        
        # Default SSH configuration (will be updated via update_ssh_config)
        self.ssh_config = {
            'hostname': dns_server,
            'username': 'admin',
            'password': 'password',
            'port': 22,
            'zone_files_path': '/etc/bind/zone',
            'configured': False
        }
        
        # Load configuration from YAML file
        self.config = self._load_zones_config()

    def _load_zones_config(self) -> Dict[str, Any]:
        """Load zones configuration from YAML file"""
        config_file = 'zones_config.yaml'
        default_config = {
            'fallback_zones': ['localhost'],
            'test_zones': ['localhost', '127.in-addr.arpa', 'local'],
            'system_zones': [
                'localhost', '127.in-addr.arpa', '0.in-addr.arpa', 
                '255.in-addr.arpa', 'root.hint', 'hint', '.', 'cache'
            ],
            'discovery': {
                'max_subdomains': 50,
                'dns_timeout': 5,
                'enable_subdomain_discovery': True,
                'enable_dns_walking': True
            }
        }
        
        if not YAML_AVAILABLE:
            logger.warning("PyYAML not available, using default configuration")
            return default_config
        
        try:
            # Look for configuration file in current directory
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                    logger.info(f"Configuration loaded from {config_file}")
                    
                    # Merge with default configuration for missing keys
                    for key, value in default_config.items():
                        if key not in config:
                            config[key] = value
                            logger.debug(f"Missing key '{key}' added with default value")
                    
                    return config
            else:
                logger.info(f"File {config_file} not found, creating with default configuration")
                # Create file with default configuration
                self._create_default_config_file(config_file, default_config)
                return default_config
                
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML file {config_file}: {e}")
            return default_config
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            return default_config

    def _create_default_config_file(self, config_file: str, config: Dict[str, Any]):
        """Create a default configuration file"""
        try:
            if YAML_AVAILABLE:
                with open(config_file, 'w', encoding='utf-8') as f:
                    yaml.dump(config, f, default_flow_style=False, allow_unicode=True, 
                            indent=2, sort_keys=False)
                logger.info(f"Default configuration file created: {config_file}")
        except Exception as e:
            logger.error(f"Error creating configuration file: {e}")

    def reload_config(self):
        """Reload configuration from YAML file"""
        self.config = self._load_zones_config()
        logger.info("Configuration reloaded")

    def update_ssh_config(self, config: Dict[str, Any]):
        """Update SSH configuration"""
        self.ssh_config.update(config)

    def test_ssh_connection(self, ssh_config: Dict[str, Any]) -> Dict[str, Any]:
        """Test SSH connection to server"""
        result = {'success': False, 'message': ''}
        
        if not PARAMIKO_AVAILABLE:
            result['message'] = 'Error: The paramiko module is not installed. Run: pip install paramiko'
            return result
        
        try:
            # Create SSH client
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Attempt connection
            ssh_client.connect(
                hostname=ssh_config['hostname'],
                port=ssh_config['port'],
                username=ssh_config['username'],
                password=ssh_config['password'],
                timeout=10
            )
            
            # Test execution of a simple command
            stdin, stdout, stderr = ssh_client.exec_command('whoami')
            user_output = stdout.read().decode().strip()
            
            if user_output == ssh_config['username']:
                result['success'] = True
                result['message'] = f'SSH connection successful as {user_output}'
            else:
                result['message'] = f'Connection established but unexpected user: {user_output}'
            
            # Test access to zone files directory
            stdin, stdout, stderr = ssh_client.exec_command(f'ls -la {ssh_config["zone_files_path"]}')
            exit_status = stdout.channel.recv_exit_status()
            
            if exit_status == 0:
                result['message'] += f' | Access to {ssh_config["zone_files_path"]} directory confirmed'
            else:
                result['message'] += f' | Warning: Limited access to {ssh_config["zone_files_path"]}'
            
            ssh_client.close()
            
        except paramiko.AuthenticationException:
            result['message'] = 'SSH authentication failed (username or password incorrect)'
        except paramiko.SSHException as e:
            result['message'] = f'SSH error: {str(e)}'
        except ConnectionRefusedError:
            result['message'] = 'Connection refused (check IP address and port)'
        except TimeoutError:
            result['message'] = 'SSH connection timeout'
        except Exception as e:
            result['message'] = f'Unexpected error: {str(e)}'
        
        return result

    def get_zones(self) -> List[str]:
        """Retrieve the list of configured DNS zones"""
        try:
            # Default common zones from configuration
            common_zones = self.config.get('fallback_zones', ['localhost'])

            # Attempt retrieval via SSH (requires configuration)
            zones = self._get_zones_from_config()
            if zones:
                return zones

            # Otherwise, return common zones
            return common_zones

        except Exception as e:
            logger.error(f"Error retrieving zones: {e}")
            return self.config.get('fallback_zones', ['localhost'])

    def _get_zones_from_config(self) -> List[str]:
        """Attempt to retrieve zones from BIND configuration file via SSH"""
        zones = []
        
        # Check if SSH is configured
        if not self.ssh_config.get('configured', False):
            logger.info("SSH not configured, using fallback zones")
            return self._get_fallback_zones()
            
        if not PARAMIKO_AVAILABLE:
            logger.warning("Paramiko not available, using fallback zones")
            return self._get_fallback_zones()
        
        try:
            logger.info("Starting automatic zone discovery via SSH...")
            logger.debug(f"SSH config: {self.ssh_config['hostname']}:{self.ssh_config['port']} as {self.ssh_config['username']}")
            
            # Strategy 1: Read BIND configuration
            logger.debug("Strategy 1: Reading BIND configuration files...")
            config_zones = self._discover_zones_from_bind_config()
            if config_zones:
                zones.extend(config_zones)
                logger.info(f"Zones found in BIND configuration: {config_zones}")
            else:
                logger.debug("No zones found in BIND configuration files")
            
            # Strategy 2: Scan zone files directory
            logger.debug("Strategy 2: Scanning zone files directories...")
            zone_file_zones = self._discover_zones_from_zone_files()
            if zone_file_zones:
                # Add zones found that are not already in the list
                new_zones = []
                for zone in zone_file_zones:
                    if zone not in zones:
                        zones.append(zone)
                        new_zones.append(zone)
                logger.info(f"New zones found in zone files: {new_zones}")
                logger.debug(f"All zones found in zone files: {zone_file_zones}")
            else:
                logger.debug("No zones found in zone files directories")
            
            # Filter and validate zones found
            logger.debug(f"Raw zones discovered: {zones}")
            valid_zones = []
            invalid_zones = []
            
            for zone in zones:
                if self._is_valid_zone_name(zone):
                    if not self._is_system_zone(zone):
                        valid_zones.append(zone)
                        logger.debug(f"Zone validated and accepted: {zone}")
                    else:
                        logger.debug(f"Zone ignored (system zone): {zone}")
                        invalid_zones.append(f"{zone} (system)")
                else:
                    logger.debug(f"Zone ignored (invalid name): {zone}")
                    invalid_zones.append(f"{zone} (invalid)")
            
            if invalid_zones:
                logger.debug(f"Ignored zones: {invalid_zones}")
            
            if valid_zones:
                logger.info(f"Total valid zones discovered: {len(valid_zones)} - {valid_zones}")
                return sorted(list(set(valid_zones)))  # Remove duplicates and sort
            else:
                logger.warning("No valid zones found via SSH, using fallback zones")
                return self._get_fallback_zones()
                
        except Exception as e:
            logger.error(f"Error in automatic zone discovery: {e}", exc_info=True)
            logger.info("Falling back to fallback zones due to error")
            return self._get_fallback_zones()

    def _discover_zones_from_bind_config(self) -> List[str]:
        """Discover zones by analyzing BIND configuration files"""
        zones = []
        
        try:
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            ssh_client.connect(
                hostname=self.ssh_config['hostname'],
                port=self.ssh_config['port'],
                username=self.ssh_config['username'],
                password=self.ssh_config['password'],
                timeout=10
            )
            
            # BIND configuration files to analyze
            config_files = [
                '/etc/bind/named.conf.local',
                '/etc/bind/named.conf',
                '/etc/named.conf',
                '/var/named/named.conf',
                '/usr/local/etc/named.conf'
            ]
            
            for config_file in config_files:
                try:
                    # Check if file exists
                    stdin, stdout, stderr = ssh_client.exec_command(f'test -f {config_file} && echo "exists" || echo "not_found"')
                    file_check = stdout.read().decode().strip()
                    
                    if file_check == "exists":
                        logger.debug(f"Analyzing configuration file: {config_file}")
                        
                        # Read file content
                        stdin, stdout, stderr = ssh_client.exec_command(f'cat {config_file}')
                        content = stdout.read().decode()
                        
                        # Analyze zone directives
                        file_zones = self._parse_bind_config_zones(content)
                        for zone in file_zones:
                            if zone not in zones:
                                zones.append(zone)
                                
                except Exception as e:
                    logger.debug(f"Error analyzing {config_file}: {e}")
                    continue
            
            ssh_client.close()
            
        except Exception as e:
            logger.error(f"SSH error in zone discovery in configuration: {e}")
        
        return zones

    def _parse_bind_config_zones(self, content: str) -> List[str]:
        """Parse the content of a BIND configuration file to extract zones"""
        zones = []
        
        # Regex for capturing zone declarations
        # Format: zone "nom.zone" { ... }
        import re
        zone_pattern = r'zone\s+"([^"]+)"\s*\{'
        
        matches = re.findall(zone_pattern, content, re.IGNORECASE)
        for match in matches:
            zone_name = match.strip()
            # Ignore common system zones
            if not self._is_system_zone(zone_name):
                zones.append(zone_name)
                logger.debug(f"Zone found in config: {zone_name}")
        
        return zones

    def _discover_zones_from_zone_files(self) -> List[str]:
        """Discover zones by listing zone files in specific directories only"""
        zones = []
        
        try:
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            ssh_client.connect(
                hostname=self.ssh_config['hostname'],
                port=self.ssh_config['port'],
                username=self.ssh_config['username'],
                password=self.ssh_config['password'],
                timeout=10
            )
            
            # Only scan the specific zone directories (not generic paths)
            specific_zone_directories = [
                '/etc/bind/zone/direct',
                '/etc/bind/zone/reverse'
            ]
            
            # Add fallback only if SSH config specifies a different path
            configured_path = self.ssh_config.get('zone_files_path', '/etc/bind/zone')
            if configured_path and configured_path not in ['/etc/bind/zone', '/etc/bind/zone/direct', '/etc/bind/zone/reverse']:
                specific_zone_directories.append(configured_path)
            
            for zone_dir in specific_zone_directories:
                try:
                    # Check if directory exists
                    stdin, stdout, stderr = ssh_client.exec_command(f'test -d {zone_dir} && echo "exists" || echo "not_found"')
                    dir_check = stdout.read().decode().strip()
                    
                    if dir_check == "exists":
                        logger.debug(f"Analyzing zone directory: {zone_dir}")
                        
                        # List only zone files with specific patterns
                        # Look for files that start with 'db.' or end with '.zone' or '.db'
                        stdin, stdout, stderr = ssh_client.exec_command(
                            f'find {zone_dir} -maxdepth 1 -type f \\( -name "db.*" -o -name "*.zone" -o -name "*.db" \\) 2>/dev/null | xargs -I {{}} basename {{}} || echo ""'
                        )
                        zone_files = stdout.read().decode().strip()
                        
                        if zone_files:
                            for zone_file in zone_files.split('\n'):
                                zone_file = zone_file.strip()
                                if zone_file and self._is_zone_file(zone_file):
                                    # Extract zone name from file name
                                    zone_name = self._extract_zone_name_from_file(zone_file)
                                    if (zone_name and 
                                        zone_name not in zones and 
                                        not self._is_system_zone(zone_name) and
                                        self._is_valid_zone_name(zone_name)):
                                        zones.append(zone_name)
                                        logger.debug(f"Zone found in files: {zone_name} (file: {zone_file} in {zone_dir})")
                        
                except Exception as e:
                    logger.debug(f"Error analyzing {zone_dir}: {e}")
                    continue
            
            ssh_client.close()
            
        except Exception as e:
            logger.error(f"SSH error in zone discovery in files: {e}")
        
        return zones

    def _is_zone_file(self, filename: str) -> bool:
        """Check if a filename represents a valid zone file"""
        if not filename or not filename.strip():
            return False
            
        filename = filename.strip().lower()
        
        # Skip hidden files, system files, and non-zone files
        skip_patterns = [
            '.',           # Hidden files
            'readme',      # Documentation
            'backup',      # Backup files
            'tmp',         # Temporary files
            'lock',        # Lock files
            'journal',     # Journal files
            'jnl',         # Journal files
            'named.conf',  # Configuration files
            'rndc.key',    # RNDC key files
            'bind.keys',   # BIND keys
            'root.hints',  # Root hints
            'managed-keys' # Managed keys
        ]
        
        # Check if filename contains any skip patterns
        for pattern in skip_patterns:
            if pattern in filename:
                return False
        
        # Accept files with zone-like patterns
        zone_patterns = [
            filename.startswith('db.'),           # db.example.com
            filename.endswith('.zone'),           # example.com.zone
            filename.endswith('.db')              # example.com.db
        ]
        
        # Also accept files that look like domain names directly
        if not any(zone_patterns):
            # Check if it looks like a domain name
            import re
            # Domain pattern or reverse zone pattern
            domain_pattern = r'^([a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]*\.)*[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]*\.?$'
            reverse_pattern = r'^(\d{1,3}\.){0,3}\d{1,3}\.in-addr\.arpa\.?$'
            ipv6_reverse_pattern = r'^[0-9a-fA-F\.]+\.ip6\.arpa\.?$'
            
            if (re.match(domain_pattern, filename) or 
                re.match(reverse_pattern, filename) or
                re.match(ipv6_reverse_pattern, filename)):
                zone_patterns.append(True)
        
        return any(zone_patterns)

    def _extract_zone_name_from_file(self, filename: str) -> str:
        """Extract zone name from file name with strict validation"""
        if not filename or not filename.strip():
            return None
            
        filename = filename.strip()
        
        # Skip files that don't look like zone files
        if not self._is_zone_file(filename):
            return None
        
        zone_name = None
        
        # Common patterns for naming zone files
        if filename.startswith('db.'):
            zone_name = filename[3:]  # Remove "db."
        elif filename.endswith('.db'):
            zone_name = filename[:-3]  # Remove ".db"
        elif filename.endswith('.zone'):
            zone_name = filename[:-5]  # Remove ".zone"
        else:
            # Direct zone name (validate it looks like a domain)
            import re
            # More flexible domain pattern including reverse zones
            domain_pattern = r'^([a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]*\.)*[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]*\.?$'
            # Also match reverse zones like 1.168.192.in-addr.arpa
            reverse_pattern = r'^(\d{1,3}\.){0,3}\d{1,3}\.in-addr\.arpa\.?$'
            # Match IPv6 reverse zones
            ipv6_reverse_pattern = r'^[0-9a-fA-F\.]+\.ip6\.arpa\.?$'
            
            if (re.match(domain_pattern, filename) or 
                re.match(reverse_pattern, filename) or
                re.match(ipv6_reverse_pattern, filename)):
                zone_name = filename
        
        if zone_name:
            # Clean up the zone name
            zone_name = zone_name.rstrip('.')
            
            # Final validation - make sure it's not empty and looks valid
            if zone_name and self._is_valid_zone_name(zone_name):
                return zone_name
        
        return None

    def _is_system_zone(self, zone_name: str) -> bool:
        """Check if a zone is a system zone to ignore"""
        system_zones = self.config.get('system_zones', [
            'localhost', '127.in-addr.arpa', '0.in-addr.arpa', 
            '255.in-addr.arpa', 'root.hint', 'hint', '.', 'cache'
        ])
        return zone_name.lower() in [z.lower() for z in system_zones]

    def _is_valid_zone_name(self, zone_name: str) -> bool:
        """Validate that a zone name is syntactically correct"""
        if not zone_name or len(zone_name.strip()) == 0:
            return False
        
        zone_name = zone_name.strip().rstrip('.')
        
        # Don't accept empty zones after cleanup
        if not zone_name:
            return False
        
        # Reverse zones are valid
        if zone_name.endswith('.in-addr.arpa') or zone_name.endswith('.ip6.arpa'):
            return True
        
        # Basic validation for domain names
        import re
        # More comprehensive domain validation
        # Allow single names like "localhost" or "local"
        if '.' not in zone_name:
            # Single word domains like "localhost", "local"
            single_word_pattern = r'^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$'
            return re.match(single_word_pattern, zone_name) is not None
        
        # Multi-part domains
        domain_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$'
        
        # Check for valid characters and structure
        if not re.match(domain_pattern, zone_name):
            return False
        
        # Additional checks
        parts = zone_name.split('.')
        for part in parts:
            if len(part) == 0 or len(part) > 63:
                return False
            if part.startswith('-') or part.endswith('-'):
                return False
        
        return True

    def _get_fallback_zones(self) -> List[str]:
        """Return fallback zones when automatic discovery fails"""
        logger.info("Using fallback zones from configuration")
        
        # Try to discover some common zones via DNS queries
        discovered_zones = []
        test_zones = self.config.get('test_zones', ['localhost', '127.in-addr.arpa', 'local'])
        
        # Configure DNS timeout from configuration
        dns_timeout = self.config.get('discovery', {}).get('dns_timeout', 5)
        original_timeout = self.resolver.timeout
        self.resolver.timeout = dns_timeout
        
        logger.debug(f"Testing zones with DNS queries (timeout: {dns_timeout}s): {test_zones}")
        
        try:
            for zone in test_zones:
                try:
                    # Test if zone exists by attempting a SOA query
                    answers = self.resolver.resolve(zone, 'SOA')
                    discovered_zones.append(zone)
                    logger.debug(f"Fallback zone detected via DNS: {zone}")
                except dns.resolver.NXDOMAIN:
                    logger.debug(f"Zone does not exist: {zone}")
                except dns.resolver.NoAnswer:
                    logger.debug(f"Zone exists but no SOA record: {zone}")
                except dns.resolver.Timeout:
                    logger.debug(f"DNS timeout for zone: {zone}")
                except Exception as e:
                    logger.debug(f"DNS error for zone {zone}: {e}")
                    continue
        finally:
            # Restore original timeout
            self.resolver.timeout = original_timeout
        
        # If no test zone worked, use defined fallback zones
        if not discovered_zones:
            discovered_zones = self.config.get('fallback_zones', ['localhost'])
            logger.info(f"No test zones detected via DNS, using configured fallback zones: {discovered_zones}")
        else:
            logger.info(f"Zones detected via DNS queries: {discovered_zones}")
        
        # Ensure we always return at least one zone
        if not discovered_zones:
            discovered_zones = ['localhost']
            logger.warning("No zones found anywhere, defaulting to localhost")
        
        return sorted(list(set(discovered_zones)))  # Remove duplicates and sort

    def get_records(self, zone: str, record_type: str = 'all') -> List[Dict[str, Any]]:
        """Retrieve DNS records for a given zone"""
        records = []

        try:
            if record_type == 'all':
                # Retrieve all record types
                record_types = ['A', 'AAAA', 'CNAME', 'MX', 'NS', 'PTR', 'SOA', 'TXT', 'SPF', 'SRV']
            elif record_type == 'direct':
                record_types = ['A', 'AAAA', 'CNAME']
            elif record_type == 'inverse':
                record_types = ['PTR']
            elif record_type == 'special':
                record_types = ['MX', 'NS', 'SOA', 'TXT', 'SPF', 'SRV']
            else:
                record_types = [record_type.upper()]

            # First, attempt zone AXFR transfer
            logger.info(f"Attempting zone transfer for {zone}")
            axfr_records = self._try_zone_transfer(zone, record_type)
            if axfr_records:
                logger.info(f"Zone transfer successful: {len(axfr_records)} records found")
                return axfr_records

            logger.info(f"Zone transfer failed, using individual queries")
            
            # If zone transfer fails, use individual queries
            zone_exists = False
            
            # Configure DNS timeout from configuration
            dns_timeout = self.config.get('discovery', {}).get('dns_timeout', 5)
            # Use an even shorter timeout for individual record queries to be more responsive
            record_query_timeout = min(dns_timeout, 0.5)  # Maximum 0.5 seconds for quick response
            original_timeout = self.resolver.timeout
            self.resolver.timeout = record_query_timeout
            
            try:
                # 1. Retrieve records from zone root
                for rtype in record_types:
                    try:
                        if rtype == 'PTR' and not zone.endswith('.arpa'):
                            continue

                        logger.debug(f"Query {rtype} for {zone}")
                        answers = self.resolver.resolve(zone, rtype)
                        zone_exists = True

                        for answer in answers:
                            record = {
                                'name': self._convert_to_relative_name(zone, zone),
                                'type': rtype,
                                'value': str(answer),
                                'ttl': answers.rrset.ttl if hasattr(answers, 'rrset') else 'N/A'
                            }
                            records.append(record)
                            logger.debug(f"Found: {record}")

                    except dns.resolver.NoAnswer:
                        continue
                    except dns.resolver.NXDOMAIN:
                        logger.debug(f"NXDOMAIN for {rtype} in {zone}")
                        continue
                    except dns.resolver.Timeout:
                        logger.warning(f"Timeout for {rtype} in {zone}")
                        continue
                    except Exception as e:
                        logger.debug(f"Error for {rtype} in {zone}: {e}")
                        continue

                # 2. If asking for all records, explore common subdomains
                if record_type == 'all' and self.config.get('discovery', {}).get('enable_subdomain_discovery', True):
                    records.extend(self._discover_subdomains(zone))
                
                # 3. If still no satisfactory result, try DNS walking approach
                if (len(records) < 3 and 
                    self.config.get('discovery', {}).get('enable_dns_walking', True)):
                    logger.info(f"Few records found ({len(records)}), trying DNS discovery")
                    walking_records = self._dns_walking(zone)
                    records.extend(walking_records)
                    
            finally:
                # Restore original timeout
                self.resolver.timeout = original_timeout

        except Exception as e:
            logger.error(f"Error retrieving records: {e}")

        # Remove duplicates
        unique_records = []
        seen = set()
        for record in records:
            key = (record['name'], record['type'], record['value'])
            if key not in seen:
                seen.add(key)
                unique_records.append(record)

        logger.info(f"Total unique records found: {len(unique_records)}")
        return unique_records

    def _discover_subdomains(self, zone: str) -> List[Dict[str, Any]]:
        """Discover current subdomains of the zone"""
        records = []
        
        # Extended list of commonly used subdomains
        common_subdomains = [
            'www', 'mail', 'ftp', 'ns1', 'ns2', 'ns3', 'ns', 'dns', 'dns1', 'dns2',
            'mx', 'mx1', 'mx2', 'smtp', 'pop', 'pop3', 'imap', 'webmail',
            'admin', 'cpanel', 'whm', 'panel', 'control',
            'blog', 'shop', 'store', 'api', 'app', 'mobile',
            'test', 'dev', 'staging', 'prod', 'demo',
            'vpn', 'remote', 'ssh', 'sftp',
            'cloud', 'cdn', 'static', 'media', 'img', 'images',
            'video', 'stream', 'live', 'chat',
            'forum', 'wiki', 'docs', 'help', 'support'
        ]
        
        # Limit the number of subdomains according to configuration
        max_subdomains = self.config.get('discovery', {}).get('max_subdomains', 50)
        subdomains_to_test = common_subdomains[:max_subdomains]
        
        # Configure DNS timeout
        dns_timeout = self.config.get('discovery', {}).get('dns_timeout', 5)
        original_timeout = self.resolver.timeout
        self.resolver.timeout = dns_timeout
        
        try:
            for subdomain in subdomains_to_test:
                full_name = f"{subdomain}.{zone}"
                
                # Test different record types for each subdomain
                for rtype in ['A', 'AAAA', 'CNAME', 'MX', 'TXT']:
                    try:
                        answers = self.resolver.resolve(full_name, rtype)
                        
                        for answer in answers:
                            record = {
                                'name': self._convert_to_relative_name(full_name, zone),
                                'type': rtype,
                                'value': str(answer),
                                'ttl': answers.rrset.ttl if hasattr(answers, 'rrset') else 'N/A'
                            }
                            records.append(record)
                            logger.debug(f"Subdomain found: {record}")
                            
                    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.Timeout):
                        continue
                    except Exception as e:
                        logger.debug(f"Error for {full_name} {rtype}: {e}")
                        continue
        finally:
            # Restore original timeout
            self.resolver.timeout = original_timeout
        
        logger.info(f"Subdomain discovery: {len(records)} records found (tested: {len(subdomains_to_test)}/{len(common_subdomains)})")
        return records

    def _dns_walking(self, zone: str) -> List[Dict[str, Any]]:
        """DNS walking technique for discovering additional records"""
        records = []
        
        try:
            # Try to retrieve NS records to obtain name servers
            ns_servers = []
            try:
                ns_answers = self.resolver.resolve(zone, 'NS')
                ns_servers = [str(ns) for ns in ns_answers]
                logger.info(f"NS servers found: {ns_servers}")
            except:
                pass
            
            # Use each NS server for specific queries
            for ns_server in ns_servers[:2]:  # Limit to 2 NS servers
                try:
                    # Create specific resolver for this NS server
                    specific_resolver = dns.resolver.Resolver()
                    
                    # Resolve NS server IP address
                    try:
                        ns_ip_answers = self.resolver.resolve(ns_server, 'A')
                        ns_ip = str(ns_ip_answers[0])
                        specific_resolver.nameservers = [ns_ip]
                        logger.info(f"Interrogating NS server {ns_server} ({ns_ip})")
                        
                        # Try specific queries on this server
                        for rtype in ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'SRV']:
                            try:
                                answers = specific_resolver.resolve(zone, rtype)
                                for answer in answers:
                                    record = {
                                        'name': self._convert_to_relative_name(zone, zone),
                                        'type': rtype,
                                        'value': str(answer),
                                        'ttl': answers.rrset.ttl if hasattr(answers, 'rrset') else 'N/A'
                                    }
                                    records.append(record)
                                    logger.debug(f"DNS Walking found: {record}")
                            except:
                                continue
                                
                    except Exception as e:
                        logger.debug(f"Error with NS server {ns_server}: {e}")
                        continue
                        
                except Exception as e:
                    logger.debug(f"General error with NS {ns_server}: {e}")
                    continue
            
            # Try some common patterns with numbers
            patterns = ['host', 'server', 'pc', 'workstation']
            for pattern in patterns:
                for i in range(1, 6):  # Test 1-5
                    test_name = f"{pattern}{i}.{zone}"
                    try:
                        answers = self.resolver.resolve(test_name, 'A')
                        for answer in answers:
                            record = {
                                'name': self._convert_to_relative_name(test_name, zone),
                                'type': 'A',
                                'value': str(answer),
                                'ttl': answers.rrset.ttl if hasattr(answers, 'rrset') else 'N/A'
                            }
                            records.append(record)
                            logger.debug(f"Pattern found: {record}")
                    except:
                        continue
                        
        except Exception as e:
            logger.error(f"Error in DNS walking: {e}")
        
        logger.info(f"DNS Walking: {len(records)} additional records found")
        return records

    def _try_zone_transfer(self, zone: str, record_type: str) -> List[Dict[str, Any]]:
        """Attempt a zone AXFR transfer"""
        records = []
        try:
            logger.info(f"Attempting zone AXFR transfer for {zone}")
            # Attempt zone transfer (requires authorization)
            zone_data = dns.zone.from_xfr(dns.query.xfr(self.dns_server, zone))

            for name, node in zone_data.nodes.items():
                for rdataset in node.rdatasets:
                    rtype = dns.rdatatype.to_text(rdataset.rdtype)

                    # Filter by type if necessary
                    if self._should_include_record(rtype, record_type):
                        for rdata in rdataset:
                            # Construct full name
                            if str(name) == '@':
                                full_name = zone
                            elif str(name) == zone:
                                full_name = zone
                            else:
                                full_name = f"{name}.{zone}" if not str(name).endswith('.') else str(name)
                            
                            record = {
                                'name': self._convert_to_relative_name(full_name, zone),
                                'type': rtype,
                                'value': str(rdata),
                                'ttl': rdataset.ttl
                            }
                            records.append(record)

            logger.info(f"Zone transfer successful: {len(records)} records retrieved")

        except dns.query.TransferError as e:
            logger.warning(f"Zone transfer refused for {zone}: {e}")
        except dns.exception.FormError as e:
            logger.warning(f"Format error during zone transfer for {zone}: {e}")
        except Exception as e:
            logger.error(f"Zone transfer failed for {zone}: {e}")

        return records

    def _should_include_record(self, rtype: str, filter_type: str) -> bool:
        """Determine if a record should be included based on filter"""
        if filter_type == 'all':
            return True
        elif filter_type == 'direct':
            return rtype in ['A', 'AAAA', 'CNAME']
        elif filter_type == 'inverse':
            return rtype == 'PTR'
        elif filter_type == 'special':
            return rtype in ['MX', 'NS', 'SOA', 'TXT', 'SPF', 'SRV']
        else:
            return rtype.upper() == filter_type.upper()

    def validate_zone(self, zone: str) -> bool:
        """Validate that a zone exists"""
        try:
            self.resolver.resolve(zone, 'SOA')
            return True
        except:
            return False

    def get_zone_info(self, zone: str) -> Dict[str, Any]:
        """Retrieve detailed information about a zone"""
        info = {
            'zone': zone,
            'exists': False,
            'soa': None,
            'ns_records': [],
            'record_count': 0
        }

        try:
            # SOA verification
            soa_answer = self.resolver.resolve(zone, 'SOA')
            info['exists'] = True
            info['soa'] = str(soa_answer[0])

            # Retrieve NS
            try:
                ns_answers = self.resolver.resolve(zone, 'NS')
                info['ns_records'] = [str(ns) for ns in ns_answers]
            except:
                pass

            # Approximate record count
            records = self.get_records(zone, 'all')
            info['record_count'] = len(records)

        except Exception as e:
            logger.error(f"Error retrieving zone info {zone}: {e}")

        return info

    def add_dns_record(self, zone: str, name: str, record_type: str, value: str, ttl: int = 3600) -> Dict[str, Any]:
        """Add a new DNS record to the specified zone"""
        result = {
            'success': False,
            'message': '',
            'record': None
        }

        try:
            # Validate parameters
            validation_result = self._validate_record_parameters(zone, name, record_type, value, ttl)
            if not validation_result['valid']:
                result['message'] = validation_result['message']
                return result

            # Normalize name - ensure it is relative
            clean_name = self._ensure_relative_name(name, zone)
            
            # Construct full name for response
            if clean_name and clean_name != '@':
                full_name = f"{clean_name}.{zone}"
            else:
                full_name = zone

            # Format record line for zone file
            record_line = self._format_record_line(clean_name, record_type, value, ttl)

            # Attempt addition via SSH
            ssh_result = self._add_record_via_ssh(zone, record_line)
            if ssh_result['success']:
                result.update(ssh_result)
                result['record'] = {
                    'name': self._convert_to_relative_name(full_name, zone),
                    'type': record_type,
                    'value': value,
                    'ttl': ttl
                }
            else:
                # Fallback: local simulation (for development/test)
                logger.warning("SSH not available, simulating record addition")
                result['success'] = True
                result['message'] = f"Record added successfully (simulation mode)"
                result['record'] = {
                    'name': self._convert_to_relative_name(full_name, zone),
                    'type': record_type,
                    'value': value,
                    'ttl': ttl
                }

        except Exception as e:
            logger.error(f"Error adding record: {e}")
            result['message'] = f"Technical error: {str(e)}"

        return result

    def _validate_record_parameters(self, zone: str, name: str, record_type: str, value: str, ttl: int) -> Dict[
        str, Any]:
        """Validate DNS record parameters"""
        validation = {'valid': True, 'message': ''}

        # Validate zone
        if not zone or not zone.strip():
            validation['valid'] = False
            validation['message'] = "Zone cannot be empty"
            return validation

        # Validate record type
        valid_types = ['A', 'AAAA', 'CNAME', 'MX', 'NS', 'PTR', 'TXT', 'SRV']
        if record_type.upper() not in valid_types:
            validation['valid'] = False
            validation['message'] = f"Unsupported record type. Valid types: {', '.join(valid_types)}"
            return validation

        # Validate value
        if not value or not value.strip():
            validation['valid'] = False
            validation['message'] = "Value cannot be empty"
            return validation

        # Validate TTL
        if ttl < 60 or ttl > 86400:
            validation['valid'] = False
            validation['message'] = "TTL must be between 60 and 86400 seconds"
            return validation

        # Specific validations by type
        record_type = record_type.upper()

        if record_type == 'A':
            if not self._is_valid_ipv4(value):
                validation['valid'] = False
                validation['message'] = "Invalid IPv4 address"

        elif record_type == 'AAAA':
            if not self._is_valid_ipv6(value):
                validation['valid'] = False
                validation['message'] = "Invalid IPv6 address"

        elif record_type == 'MX':
            # Format: "priority server"
            parts = value.strip().split()
            if len(parts) != 2 or not parts[0].isdigit():
                validation['valid'] = False
                validation['message'] = "Invalid MX format. Use: 'priority server.domain.'"

        elif record_type == 'CNAME':
            if not value.endswith('.'):
                validation['valid'] = False
                validation['message'] = "CNAME records must end with a dot"

        return validation

    def _is_valid_ipv4(self, ip: str) -> bool:
        """Validate an IPv4 address"""
        try:
            parts = ip.split('.')
            if len(parts) != 4:
                return False
            for part in parts:
                if not 0 <= int(part) <= 255:
                    return False
            return True
        except:
            return False

    def _is_valid_ipv6(self, ip: str) -> bool:
        """Validate an IPv6 address"""
        try:
            import socket
            socket.inet_pton(socket.AF_INET6, ip)
            return True
        except:
            return False

    def _format_record_line(self, name: str, record_type: str, value: str, ttl: int) -> str:
        """Format a DNS record line for zone file"""
        # Normalize name - empty or None becomes @
        if not name or name.strip() == '':
            name = '@'
        else:
            name = name.strip()
        
        # Format according to type
        if record_type.upper() == 'MX':
            return f"{name:<30} {ttl:<8} IN {record_type:<8} {value}"
        else:
            return f"{name:<30} {ttl:<8} IN {record_type:<8} {value}"

    def _add_record_via_ssh(self, zone: str, record_line: str) -> Dict[str, Any]:
        """Add a record via SSH by actually modifying the zone file"""
        result = {'success': False, 'message': ''}
        
        if not PARAMIKO_AVAILABLE:
            result['message'] = 'Error: The paramiko module is not installed. Run: pip install paramiko'
            return result
        
        if not self.ssh_config.get('configured'):
            result['message'] = 'SSH configuration required to modify zone files'
            return result
        
        try:
            # Create SSH client
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # SSH connection
            ssh_client.connect(
                hostname=self.ssh_config['hostname'],
                port=self.ssh_config['port'],
                username=self.ssh_config['username'],
                password=self.ssh_config['password'],
                timeout=10
            )
            
            # Find the zone file path
            zone_file_path = self._find_existing_zone_file(zone)
            logger.debug(f"Using zone file path: {zone_file_path}")
            
            # Check if zone file exists
            stdin, stdout, stderr = ssh_client.exec_command(f'test -f {zone_file_path} && echo "exists" || echo "not_found"')
            file_check = stdout.read().decode().strip()
            
            if file_check == "not_found":
                result['message'] = f'Zone file {zone_file_path} not found'
                ssh_client.close()
                return result
            
            # Create backup directory if it doesn't exist
            stdin, stdout, stderr = ssh_client.exec_command('mkdir -p /etc/bind/backup')
            
            # Create file backup in the backup directory
            backup_cmd = f'cp {zone_file_path} /etc/bind/backup/db.{zone}.backup.$(date +%Y%m%d_%H%M%S)'
            stdin, stdout, stderr = ssh_client.exec_command(backup_cmd)
            backup_status = stdout.channel.recv_exit_status()
            
            if backup_status != 0:
                result['message'] = 'Error creating backup in /etc/bind/backup'
                ssh_client.close()
                return result
            
            # Read current file content
            stdin, stdout, stderr = ssh_client.exec_command(f'cat {zone_file_path}')
            current_content = stdout.read().decode()
            
            # Add new record before end line
            lines = current_content.split('\n')
            new_lines = []
            record_added = False
            
            for line in lines:
                new_lines.append(line)
                # Add record after last existing record and before end comment lines
                if not record_added and line.strip() and not line.startswith(';') and not line.startswith('$'):
                    # If we find a record line, we will add our record after the corresponding section
                    if any(rtype in line.upper() for rtype in ['IN A', 'IN AAAA', 'IN CNAME', 'IN MX', 'IN NS', 'IN TXT']):
                        continue
                elif not record_added and (line.strip() == '' or line.startswith(';')):
                    # Add our record before empty or end comment lines
                    new_lines.insert(-1, record_line)
                    record_added = True
            
            # If record not added, add to end
            if not record_added:
                new_lines.insert(-1, record_line)
            
            # Increment serial number in SOA
            new_content_lines = []
            for line in new_lines:
                if 'SOA' in line.upper() and ';' not in line.split('SOA')[0]:
                    # Found SOA line, we will increment serial in following lines
                    new_content_lines.append(line)
                elif line.strip().isdigit() and len(line.strip()) == 10:
                    # Probable serial number (YYYYMMDDNN)
                    try:
                        serial = int(line.strip())
                        new_serial = serial + 1
                        new_content_lines.append(line.replace(str(serial), str(new_serial)))
                    except:
                        new_content_lines.append(line)
                else:
                    new_content_lines.append(line)
            
            new_content = '\n'.join(new_content_lines)
            
            # Write new content to temporary file
            temp_file = f'/tmp/zone_{zone}_{int(time.time())}'
            sftp = ssh_client.open_sftp()
            
            with sftp.file(temp_file, 'w') as f:
                f.write(new_content)
            
            # Validate zone file syntax
            stdin, stdout, stderr = ssh_client.exec_command(f'named-checkzone {zone} {temp_file}')
            validation_status = stdout.channel.recv_exit_status()
            validation_output = stderr.read().decode()
            
            if validation_status != 0:
                result['message'] = f'Zone validation error: {validation_output}'
                # Clean temporary file
                ssh_client.exec_command(f'rm -f {temp_file}')
                sftp.close()
                ssh_client.close()
                return result
            
            # Replace original zone file
            stdin, stdout, stderr = ssh_client.exec_command(f'mv {temp_file} {zone_file_path}')
            move_status = stdout.channel.recv_exit_status()
            
            if move_status != 0:
                result['message'] = 'Error replacing zone file'
                sftp.close()
                ssh_client.close()
                return result
            
            # Reload BIND configuration
            stdin, stdout, stderr = ssh_client.exec_command('rndc reload')
            reload_status = stdout.channel.recv_exit_status()
            reload_output = stderr.read().decode()
            
            if reload_status == 0:
                result['success'] = True
                result['message'] = 'Record added successfully and DNS server reloaded'
            else:
                result['success'] = True  # Record added even if reload failed
                result['message'] = f'Record added but DNS reload failed: {reload_output}'
            
            sftp.close()
            ssh_client.close()
            
        except paramiko.AuthenticationException:
            result['message'] = 'SSH authentication failed'
        except paramiko.SSHException as e:
            result['message'] = f'SSH error: {str(e)}'
        except Exception as e:
            logger.error(f"SSH error adding record: {e}")
            result['message'] = f'Technical SSH error: {str(e)}'
            
        return result

    def _find_existing_zone_file(self, zone: str) -> str:
        """Find the existing zone file path by searching in multiple locations"""
        # Possible paths for the zone file - prioritize specific directories
        possible_paths = [
            # New structure - highest priority
            f"/etc/bind/zone/direct/db.{zone}",
            f"/etc/bind/zone/reverse/db.{zone}",
            f"/etc/bind/zone/direct/{zone}.zone",
            f"/etc/bind/zone/reverse/{zone}.zone",
            f"/etc/bind/zone/direct/{zone}",
            f"/etc/bind/zone/reverse/{zone}",
            # Only add configured path if it's different and specific
        ]
        
        # Add configured path only if it's a specific directory
        configured_path = self.ssh_config.get('zone_files_path', '/etc/bind/zone')
        if configured_path and configured_path not in ['/etc/bind/zone', '/etc/bind/zone/direct', '/etc/bind/zone/reverse']:
            # Only add if it looks like a specific zone directory
            if 'zone' in configured_path.lower() or 'dns' in configured_path.lower():
                possible_paths.extend([
                    f"{configured_path}/db.{zone}",
                    f"{configured_path}/{zone}.zone",
                    f"{configured_path}/{zone}.db",
                    f"{configured_path}/{zone}"
                ])
        
        try:
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            ssh_client.connect(
                hostname=self.ssh_config['hostname'],
                port=self.ssh_config['port'],
                username=self.ssh_config['username'],
                password=self.ssh_config['password'],
                timeout=10
            )
            
            for path in possible_paths:
                try:
                    stdin, stdout, stderr = ssh_client.exec_command(f'test -f {path} && echo "exists" || echo "not_found"')
                    file_check = stdout.read().decode().strip()
                    
                    if file_check == "exists":
                        logger.debug(f"Found zone file for {zone} at: {path}")
                        ssh_client.close()
                        return path
                except Exception as e:
                    logger.debug(f"Error checking {path}: {e}")
                    continue
            
            ssh_client.close()
            
        except Exception as e:
            logger.error(f"SSH error finding zone file for {zone}: {e}")
        
        # If not found, return the default path based on zone type
        return self._get_zone_file_path(zone)

    def _get_zone_file_path(self, zone: str) -> str:
        """Determine the correct file path for a zone based on its type"""
        # Check if it's a reverse zone
        if zone.endswith('.in-addr.arpa') or zone.endswith('.ip6.arpa'):
            # Reverse zone - goes in reverse directory
            return f"/etc/bind/zone/reverse/db.{zone}"
        else:
            # Direct zone - goes in direct directory
            return f"/etc/bind/zone/direct/db.{zone}"

    def get_supported_record_types(self) -> List[Dict[str, str]]:
        """Return the list of supported record types with their descriptions"""
        return [
            {'type': 'A', 'description': 'IPv4 address'},
            {'type': 'AAAA', 'description': 'IPv6 address'},
            {'type': 'CNAME', 'description': 'Canonical name (alias)'},
            {'type': 'MX', 'description': 'Mail exchange'},
            {'type': 'NS', 'description': 'Name server'},
            {'type': 'PTR', 'description': 'Pointer (reverse resolution)'},
            {'type': 'TXT', 'description': 'Free text'},
            {'type': 'SRV', 'description': 'Service'}
        ]

    def delete_dns_record(self, zone: str, name: str, record_type: str, value: str) -> Dict[str, Any]:
        """Delete a DNS record from the specified zone"""
        result = {
            'success': False,
            'message': '',
            'record': None
        }

        if not PARAMIKO_AVAILABLE:
            result['message'] = 'Error: The paramiko module is not installed. Run: pip install paramiko'
            return result
        
        if not self.ssh_config.get('configured'):
            result['message'] = 'SSH configuration required to modify zone files'
            return result
        
        try:
            # Create SSH client
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # SSH connection
            ssh_client.connect(
                hostname=self.ssh_config['hostname'],
                port=self.ssh_config['port'],
                username=self.ssh_config['username'],
                password=self.ssh_config['password'],
                timeout=10
            )
            
            # Find the zone file path
            zone_file_path = self._find_existing_zone_file(zone)
            logger.debug(f"Using zone file path for deletion: {zone_file_path}")
            
            # Check if zone file exists
            stdin, stdout, stderr = ssh_client.exec_command(f'test -f {zone_file_path} && echo "exists" || echo "not_found"')
            file_check = stdout.read().decode().strip()
            
            if file_check == "not_found":
                result['message'] = f'Zone file {zone_file_path} not found'
                ssh_client.close()
                return result
            
            # Create backup directory if it doesn't exist
            stdin, stdout, stderr = ssh_client.exec_command('mkdir -p /etc/bind/backup')
            
            # Create file backup in the backup directory
            backup_cmd = f'cp {zone_file_path} /etc/bind/backup/db.{zone}.backup.$(date +%Y%m%d_%H%M%S)'
            stdin, stdout, stderr = ssh_client.exec_command(backup_cmd)
            backup_status = stdout.channel.recv_exit_status()
            
            if backup_status != 0:
                result['message'] = 'Error creating backup in /etc/bind/backup'
                ssh_client.close()
                return result
            
            # Read current file content
            stdin, stdout, stderr = ssh_client.exec_command(f'cat {zone_file_path}')
            current_content = stdout.read().decode()
            
            # Normalize values for search
            search_name = self._normalize_name_for_search(name, zone)
            search_value = value.strip()
            search_type = record_type.upper()
            
            logger.info(f"Searching for record: name='{search_name}', type='{search_type}', value='{search_value}'")
            
            # Delete corresponding record
            lines = current_content.split('\n')
            new_lines = []
            record_found = False
            records_checked = 0
            
            for line_num, line in enumerate(lines, 1):
                original_line = line
                line_stripped = line.strip()
                
                # Ignore empty and comment lines
                if not line_stripped or line_stripped.startswith(';') or line_stripped.startswith('$'):
                    new_lines.append(line)
                    continue
                
                # Check if it's a DNS record line
                if self._is_dns_record_line(line, search_type):
                    records_checked += 1
                    
                    # Extract line components
                    record_parts = self._parse_dns_record_line(line)
                    if record_parts:
                        line_name = self._normalize_name_for_search(record_parts['name'], zone)
                        line_type = record_parts['type'].upper()
                        line_value = record_parts['value'].strip()
                        
                        logger.debug(f"Line {line_num}: name='{line_name}', type='{line_type}', value='{line_value}'")
                        
                        # Check match
                        if (line_type == search_type and 
                            self._values_match(line_value, search_value, search_type) and
                            self._names_match(line_name, search_name, zone)):
                            
                            record_found = True
                            logger.info(f"Record found and deleted at line {line_num}: {original_line}")
                            continue  # Do not add this line (= deletion)
                
                new_lines.append(line)
            
            logger.info(f"Search completed: {records_checked} records checked, found: {record_found}")
            
            if not record_found:
                result['message'] = f'Record not found in zone file. Checked: {records_checked} records.'
                ssh_client.close()
                return result
            
            # Increment serial number in SOA
            new_content_lines = []
            for line in new_lines:
                if 'SOA' in line.upper() and ';' not in line.split('SOA')[0]:
                    new_content_lines.append(line)
                elif line.strip().isdigit() and len(line.strip()) == 10:
                    try:
                        serial = int(line.strip())
                        new_serial = serial + 1
                        new_content_lines.append(line.replace(str(serial), str(new_serial)))
                    except:
                        new_content_lines.append(line)
                else:
                    new_content_lines.append(line)
            
            new_content = '\n'.join(new_content_lines)
            
            # Write new content to temporary file
            temp_file = f'/tmp/zone_{zone}_{int(time.time())}'
            sftp = ssh_client.open_sftp()
            
            with sftp.file(temp_file, 'w') as f:
                f.write(new_content)
            
            # Validate zone file syntax
            stdin, stdout, stderr = ssh_client.exec_command(f'named-checkzone {zone} {temp_file}')
            validation_status = stdout.channel.recv_exit_status()
            validation_output = stderr.read().decode()
            
            if validation_status != 0:
                result['message'] = f'Zone validation error: {validation_output}'
                ssh_client.exec_command(f'rm -f {temp_file}')
                sftp.close()
                ssh_client.close()
                return result
            
            # Replace original zone file
            stdin, stdout, stderr = ssh_client.exec_command(f'mv {temp_file} {zone_file_path}')
            move_status = stdout.channel.recv_exit_status()
            
            if move_status != 0:
                result['message'] = 'Error replacing zone file'
                sftp.close()
                ssh_client.close()
                return result
            
            # Reload BIND configuration
            stdin, stdout, stderr = ssh_client.exec_command('rndc reload')
            reload_status = stdout.channel.recv_exit_status()
            reload_output = stderr.read().decode()
            
            if reload_status == 0:
                result['success'] = True
                result['message'] = 'Record deleted successfully and DNS server reloaded'
            else:
                result['success'] = True
                result['message'] = f'Record deleted but DNS reload failed: {reload_output}'
            
            sftp.close()
            ssh_client.close()
            
        except paramiko.AuthenticationException:
            result['message'] = 'SSH authentication failed'
        except paramiko.SSHException as e:
            result['message'] = f'SSH error: {str(e)}'
        except Exception as e:
            logger.error(f"SSH error deleting record: {e}")
            result['message'] = f'Technical SSH error: {str(e)}'
            
        return result

    def _normalize_name_for_search(self, name: str, zone: str) -> str:
        """Normalize a name for search in zone files"""
        if not name or name.strip() == '':
            return '@'
        
        name = name.strip()
        
        # If name ends with a dot, remove it for comparison
        if name.endswith('.'):
            name = name[:-1]
        
        # If name ends with the zone, remove the zone
        if name.endswith('.' + zone):
            name = name[:-len('.' + zone)]
        elif name == zone:
            name = '@'
        
        return name if name else '@'

    def _is_dns_record_line(self, line: str, record_type: str) -> bool:
        """Check if a line contains a DNS record of the specified type"""
        line_upper = line.upper()
        return (
            'IN' in line_upper and 
            record_type.upper() in line_upper and
            not line.strip().startswith(';') and
            not line.strip().startswith('$')
        )

    def _parse_dns_record_line(self, line: str) -> Dict[str, str]:
        """Parse a DNS record line and return its components"""
        parts = line.split()
        if len(parts) < 4:
            return None
        
        try:
            # Typical format: name TTL IN type value
            # or: name IN type value
            name = parts[0]
            
            # Find "IN" index
            in_index = -1
            for i, part in enumerate(parts):
                if part.upper() == 'IN':
                    in_index = i
                    break
            
            if in_index == -1:
                return None
            
            record_type = parts[in_index + 1] if in_index + 1 < len(parts) else ''
            value = ' '.join(parts[in_index + 2:]) if in_index + 2 < len(parts) else ''
            
            return {
                'name': name,
                'type': record_type,
                'value': value
            }
        except:
            return None

    def _values_match(self, value1: str, value2: str, record_type: str) -> bool:
        """Compare two DNS record values based on type"""
        v1 = value1.strip().rstrip('.')
        v2 = value2.strip().rstrip('.')
        
        # For MX records, compare only value after priority
        if record_type.upper() == 'MX':
            v1_parts = v1.split()
            v2_parts = v2.split()
            if len(v1_parts) >= 2 and len(v2_parts) >= 2:
                return v1_parts[1].rstrip('.') == v2_parts[1].rstrip('.')
            return v1 == v2
        
        return v1 == v2

    def _names_match(self, name1: str, name2: str, zone: str) -> bool:
        """Compare two DNS record names"""
        n1 = self._normalize_name_for_search(name1, zone)
        n2 = self._normalize_name_for_search(name2, zone)
        return n1 == n2

    def update_dns_record(self, zone: str, original: Dict[str, str], updated: Dict[str, Any]) -> Dict[str, Any]:
        """Modify a DNS record in the specified zone"""
        result = {
            'success': False,
            'message': '',
            'record': None
        }

        if not PARAMIKO_AVAILABLE:
            result['message'] = 'Error: The paramiko module is not installed. Run: pip install paramiko'
            return result
        
        if not self.ssh_config.get('configured'):
            result['message'] = 'SSH configuration required to modify zone files'
            return result

        # Validate new record parameters
        validation_result = self._validate_record_parameters(
            zone, updated['name'], updated['type'], updated['value'], updated['ttl']
        )
        if not validation_result['valid']:
            result['message'] = validation_result['message']
            return result
        
        try:
            # Create SSH client
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # SSH connection
            ssh_client.connect(
                hostname=self.ssh_config['hostname'],
                port=self.ssh_config['port'],
                username=self.ssh_config['username'],
                password=self.ssh_config['password'],
                timeout=10
            )
            
            # Find the zone file path
            zone_file_path = self._find_existing_zone_file(zone)
            logger.debug(f"Using zone file path for update: {zone_file_path}")
            
            # Check if zone file exists
            stdin, stdout, stderr = ssh_client.exec_command(f'test -f {zone_file_path} && echo "exists" || echo "not_found"')
            file_check = stdout.read().decode().strip()
            
            if file_check == "not_found":
                result['message'] = f'Zone file {zone_file_path} not found'
                ssh_client.close()
                return result
            
            # Create backup directory if it doesn't exist
            stdin, stdout, stderr = ssh_client.exec_command('mkdir -p /etc/bind/backup')
            
            # Create file backup in the backup directory
            backup_cmd = f'cp {zone_file_path} /etc/bind/backup/db.{zone}.backup.$(date +%Y%m%d_%H%M%S)'
            stdin, stdout, stderr = ssh_client.exec_command(backup_cmd)
            backup_status = stdout.channel.recv_exit_status()
            
            if backup_status != 0:
                result['message'] = 'Error creating backup in /etc/bind/backup'
                ssh_client.close()
                return result
            
            # Read current file content
            stdin, stdout, stderr = ssh_client.exec_command(f'cat {zone_file_path}')
            current_content = stdout.read().decode()
            
            # Normalize values for search
            search_name = self._normalize_name_for_search(original['name'], zone)
            search_value = original['value'].strip()
            search_type = original['type'].upper()
            
            logger.info(f"Searching for record to modify: name='{search_name}', type='{search_type}', value='{search_value}'")
            
            # Replace corresponding record
            lines = current_content.split('\n')
            new_lines = []
            record_found = False
            records_checked = 0
            
            # Normalize updated record name
            clean_updated_name = self._ensure_relative_name(updated['name'], zone)
            updated_copy = updated.copy()
            updated_copy['name'] = clean_updated_name
            
            # Create new record line
            new_record_line = self._format_record_line(
                updated_copy['name'], updated_copy['type'], updated_copy['value'], updated_copy['ttl']
            )
            
            for line_num, line in enumerate(lines, 1):
                original_line = line
                line_stripped = line.strip()
                
                # Ignore empty and comment lines
                if not line_stripped or line_stripped.startswith(';') or line_stripped.startswith('$'):
                    new_lines.append(line)
                    continue
                
                # Check if it's a DNS record line
                if self._is_dns_record_line(line, search_type):
                    records_checked += 1
                    
                    # Extract line components
                    record_parts = self._parse_dns_record_line(line)
                    if record_parts:
                        line_name = self._normalize_name_for_search(record_parts['name'], zone)
                        line_type = record_parts['type'].upper()
                        line_value = record_parts['value'].strip()
                        
                        logger.debug(f"Line {line_num}: name='{line_name}', type='{line_type}', value='{line_value}'")
                        
                        # Check match
                        if (line_type == search_type and 
                            self._values_match(line_value, search_value, search_type) and
                            self._names_match(line_name, search_name, zone)):
                            
                            new_lines.append(new_record_line)
                            record_found = True
                            logger.info(f"Record found and modified at line {line_num}: {original_line} -> {new_record_line}")
                        else:
                            new_lines.append(line)
                    else:
                        new_lines.append(line)
                else:
                    new_lines.append(line)
            
            logger.info(f"Search completed: {records_checked} records checked, found: {record_found}")
            
            if not record_found:
                result['message'] = f'Original record not found in zone file. Checked: {records_checked} records.'
                ssh_client.close()
                return result
            
            # Increment serial number in SOA
            new_content_lines = []
            for line in new_lines:
                if 'SOA' in line.upper() and ';' not in line.split('SOA')[0]:
                    new_content_lines.append(line)
                elif line.strip().isdigit() and len(line.strip()) == 10:
                    try:
                        serial = int(line.strip())
                        new_serial = serial + 1
                        new_content_lines.append(line.replace(str(serial), str(new_serial)))
                    except:
                        new_content_lines.append(line)
                else:
                    new_content_lines.append(line)
            
            new_content = '\n'.join(new_content_lines)
            
            # Write new content to temporary file
            temp_file = f'/tmp/zone_{zone}_{int(time.time())}'
            sftp = ssh_client.open_sftp()
            
            with sftp.file(temp_file, 'w') as f:
                f.write(new_content)
            
            # Validate zone file syntax
            stdin, stdout, stderr = ssh_client.exec_command(f'named-checkzone {zone} {temp_file}')
            validation_status = stdout.channel.recv_exit_status()
            validation_output = stderr.read().decode()
            
            if validation_status != 0:
                result['message'] = f'Zone validation error: {validation_output}'
                ssh_client.exec_command(f'rm -f {temp_file}')
                sftp.close()
                ssh_client.close()
                return result
            
            # Replace original zone file
            stdin, stdout, stderr = ssh_client.exec_command(f'mv {temp_file} {zone_file_path}')
            move_status = stdout.channel.recv_exit_status()
            
            if move_status != 0:
                result['message'] = 'Error replacing zone file'
                sftp.close()
                ssh_client.close()
                return result
            
            # Reload BIND configuration
            stdin, stdout, stderr = ssh_client.exec_command('rndc reload')
            reload_status = stdout.channel.recv_exit_status()
            reload_output = stderr.read().decode()
            
            if reload_status == 0:
                result['success'] = True
                result['message'] = 'Record modified successfully and DNS server reloaded'
            else:
                result['success'] = True
                result['message'] = f'Record modified but DNS reload failed: {reload_output}'
            
            # Construct full name for response
            if updated_copy['name'] and not updated_copy['name'].endswith('.') and updated_copy['name'] != '@':
                full_name = f"{updated_copy['name']}.{zone}"
            elif updated_copy['name'] == '@':
                full_name = zone
            else:
                full_name = updated_copy['name']
            
            result['record'] = {
                'name': self._convert_to_relative_name(full_name, zone),
                'type': updated_copy['type'],
                'value': updated_copy['value'],
                'ttl': updated_copy['ttl']
            }
            
            sftp.close()
            ssh_client.close()
            
        except paramiko.AuthenticationException:
            result['message'] = 'SSH authentication failed'
        except paramiko.SSHException as e:
            result['message'] = f'SSH error: {str(e)}'
        except Exception as e:
            logger.error(f"SSH error modifying record: {e}")
            result['message'] = f'Technical SSH error: {str(e)}'
            
        return result

    def _convert_to_relative_name(self, full_name: str, zone: str) -> str:
        """Convert a full name (FQDN) to a relative name for display"""
        if not full_name or not zone:
            return full_name
        
        full_name = full_name.strip().rstrip('.')
        zone = zone.strip().rstrip('.')
        
        # If name is exactly the zone, return @
        if full_name == zone:
            return '@'
        
        # If name ends with .zone, remove the zone
        if full_name.endswith('.' + zone):
            relative_name = full_name[:-len('.' + zone)]
            return relative_name if relative_name else '@'
        
        # Otherwise, return name as is
        return full_name

    def _ensure_relative_name(self, name: str, zone: str) -> str:
        """Ensure a name is relative (not fully qualified) before using it"""
        if not name or name.strip() == '':
            return ''
        
        name = name.strip()
        zone = zone.strip().rstrip('.')
        
        # If it's @, leave it as is
        if name == '@':
            return name
        
        # Remove final dot if present
        if name.endswith('.'):
            name = name[:-1]
        
        # If name already contains the zone, keep only the relative part
        if '.' + zone in name:
            parts = name.split('.' + zone)
            if len(parts) >= 2 and parts[1] == '':  # Ends with .zone
                return parts[0] if parts[0] else '@'
        
        # If name is exactly the zone, return @
        if name == zone:
            return '@'
        
        return name