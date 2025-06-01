from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, session
from utils import DNSManager
import logging

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here-change-in-production'

# DNS configuration
DNS_SERVER = '192.168.1.201'
dns_manager = DNSManager(DNS_SERVER)

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.route('/')
def index():
    """Main application page"""
    try:
        zones = dns_manager.get_zones()
        ssh_configured = 'ssh_config' in session and session['ssh_config'].get('configured', False)
        return render_template('index.html', zones=zones, ssh_configured=ssh_configured)
    except Exception as e:
        logger.error(f"Error loading main page: {e}")
        return render_template('index.html', zones=[], ssh_configured=False, 
                               error="DNS server connection error")


@app.route('/ssh-config')
def ssh_config_form():
    """SSH configuration page"""
    current_config = session.get('ssh_config', {})
    return render_template('ssh_config.html', config=current_config)


@app.route('/api/ssh-config', methods=['POST'])
def api_ssh_config():
    """API to configure SSH settings"""
    try:
        # Get form data
        hostname = request.form.get('hostname', '').strip()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        port = request.form.get('port', '22').strip()
        zone_files_path = request.form.get('zone_files_path', '/etc/bind/zones').strip()

        # Basic validation
        if not all([hostname, username, password]):
            return jsonify({
                'success': False,
                'message': 'Hostname, username and password are required'
            }), 400

        # Port conversion
        try:
            port = int(port)
        except ValueError:
            return jsonify({
                'success': False,
                'message': 'Port must be an integer'
            }), 400

        # SSH configuration
        ssh_config = {
            'hostname': hostname,
            'username': username,
            'password': password,
            'port': port,
            'zone_files_path': zone_files_path,
            'configured': True
        }

        # Test SSH connection
        test_result = dns_manager.test_ssh_connection(ssh_config)
        
        if test_result['success']:
            # Save in session
            session['ssh_config'] = ssh_config
            session.permanent = True
            
            # Update DNS manager configuration
            dns_manager.update_ssh_config(ssh_config)
            
            logger.info(f"SSH configuration successful for {username}@{hostname}")
            return jsonify({
                'success': True,
                'message': 'SSH configuration successful and connection tested successfully'
            })
        else:
            return jsonify({
                'success': False,
                'message': f'SSH connection test failed: {test_result["message"]}'
            }), 400

    except Exception as e:
        logger.error(f"Error during SSH configuration: {e}")
        return jsonify({
            'success': False,
            'message': f'Technical error during configuration: {str(e)}'
        }), 500


@app.route('/api/ssh-test', methods=['POST'])
def api_ssh_test():
    """API to test SSH connection without saving"""
    try:
        data = request.get_json()
        
        ssh_config = {
            'hostname': data.get('hostname', ''),
            'username': data.get('username', ''),
            'password': data.get('password', ''),
            'port': int(data.get('port', 22)),
            'zone_files_path': data.get('zone_files_path', '/etc/bind/zones')
        }

        result = dns_manager.test_ssh_connection(ssh_config)
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error during SSH test: {e}")
        return jsonify({
            'success': False,
            'message': f'Technical error during test: {str(e)}'
        }), 500


@app.route('/add-record')
def add_record_form():
    """Page to add a new DNS record"""
    try:
        zones = dns_manager.get_zones()
        record_types = dns_manager.get_supported_record_types()
        return render_template('add_record.html', zones=zones, record_types=record_types)
    except Exception as e:
        logger.error(f"Error loading add page: {e}")
        return render_template('add_record.html', zones=[], record_types=[],
                               error="DNS server connection error")


@app.route('/api/add-record', methods=['POST'])
def api_add_record():
    """API to add a new DNS record"""
    try:
        # Check if SSH is configured
        ssh_config = session.get('ssh_config')
        if ssh_config and ssh_config.get('configured'):
            dns_manager.update_ssh_config(ssh_config)

        # Get form data
        zone = request.form.get('zone', '').strip()
        name = request.form.get('name', '').strip()
        record_type = request.form.get('type', '').strip()
        value = request.form.get('value', '').strip()
        ttl = request.form.get('ttl', '3600').strip()

        # Basic validation
        if not all([zone, record_type, value]):
            return jsonify({
                'success': False,
                'message': 'Zone, type and value are required'
            }), 400

        # TTL conversion
        try:
            ttl = int(ttl)
        except ValueError:
            return jsonify({
                'success': False,
                'message': 'TTL must be an integer'
            }), 400

        # Add record
        result = dns_manager.add_dns_record(zone, name, record_type, value, ttl)
        
        if result['success']:
            logger.info(f"Record added successfully: {name}.{zone} {record_type} {value}")
            return jsonify(result)
        else:
            logger.warning(f"Record addition failed: {result['message']}")
            return jsonify(result), 400

    except Exception as e:
        logger.error(f"Error adding record: {e}")
        return jsonify({
            'success': False,
            'message': f'Technical error during addition: {str(e)}'
        }), 500


@app.route('/api/record-types')
def get_record_types():
    """API to retrieve supported record types"""
    try:
        record_types = dns_manager.get_supported_record_types()
        return jsonify({
            'success': True,
            'record_types': record_types
        })
    except Exception as e:
        logger.error(f"Error retrieving record types: {e}")
        return jsonify({
            'success': False,
            'error': f'Error retrieving types: {str(e)}'
        }), 500


@app.route('/api/records')
def get_records():
    """API to retrieve DNS records"""
    try:
        zone = request.args.get('zone', '')
        record_type = request.args.get('type', 'all')

        if not zone:
            return jsonify({'error': 'Zone required'}), 400

        records = dns_manager.get_records(zone, record_type)
        return jsonify({
            'success': True,
            'records': records,
            'zone': zone,
            'type': record_type
        })

    except Exception as e:
        logger.error(f"Error retrieving records: {e}")
        return jsonify({
            'success': False,
            'error': f'Error retrieving records: {str(e)}'
        }), 500


@app.route('/api/zones')
def get_zones():
    """API to retrieve the list of DNS zones"""
    try:
        zones = dns_manager.get_zones()
        return jsonify({
            'success': True,
            'zones': zones
        })
    except Exception as e:
        logger.error(f"Error retrieving zones: {e}")
        return jsonify({
            'success': False,
            'error': f'Error retrieving zones: {str(e)}'
        }), 500


@app.route('/api/zones/refresh', methods=['POST'])
def refresh_zones():
    """API to force automatic zone discovery via SSH"""
    try:
        # Check if SSH is configured
        ssh_config = session.get('ssh_config')
        if not ssh_config or not ssh_config.get('configured'):
            return jsonify({
                'success': False,
                'error': 'SSH configuration required for automatic zone discovery'
            }), 400

        # Update SSH configuration in DNS manager
        dns_manager.update_ssh_config(ssh_config)
        
        # Force zone rediscovery by clearing cache if necessary
        logger.info("Triggering automatic zone discovery...")
        
        # Call zone discovery method directly
        zones = dns_manager._get_zones_from_config()
        
        if zones:
            logger.info(f"Discovery successful: {len(zones)} zones found")
            return jsonify({
                'success': True,
                'zones': zones,
                'message': f'{len(zones)} zones automatically discovered',
                'discovery_method': 'ssh_automatic'
            })
        else:
            # Use fallback zones
            fallback_zones = dns_manager._get_fallback_zones()
            return jsonify({
                'success': True,
                'zones': fallback_zones,
                'message': 'No zones discovered via SSH, using fallback zones',
                'discovery_method': 'fallback'
            })

    except Exception as e:
        logger.error(f"Error refreshing zones: {e}")
        return jsonify({
            'success': False,
            'error': f'Error refreshing zones: {str(e)}'
        }), 500


@app.route('/api/delete-record', methods=['POST'])
def api_delete_record():
    """API to delete a DNS record"""
    try:
        # Check if SSH is configured
        ssh_config = session.get('ssh_config')
        if ssh_config and ssh_config.get('configured'):
            dns_manager.update_ssh_config(ssh_config)

        # Get data
        data = request.get_json()
        zone = data.get('zone', '').strip()
        name = data.get('name', '').strip()
        record_type = data.get('type', '').strip()
        value = data.get('value', '').strip()

        # Basic validation
        if not all([zone, record_type, value]):
            return jsonify({
                'success': False,
                'message': 'Zone, type and value are required'
            }), 400

        # Delete record
        result = dns_manager.delete_dns_record(zone, name, record_type, value)
        
        if result['success']:
            logger.info(f"Record deleted successfully: {name}.{zone} {record_type} {value}")
            return jsonify(result)
        else:
            logger.warning(f"Record deletion failed: {result['message']}")
            return jsonify(result), 400

    except Exception as e:
        logger.error(f"Error deleting record: {e}")
        return jsonify({
            'success': False,
            'message': f'Technical error during deletion: {str(e)}'
        }), 500


@app.route('/api/update-record', methods=['POST'])
def api_update_record():
    """API to modify a DNS record"""
    try:
        # Check if SSH is configured
        ssh_config = session.get('ssh_config')
        if ssh_config and ssh_config.get('configured'):
            dns_manager.update_ssh_config(ssh_config)

        # Get data
        data = request.get_json()
        zone = data.get('zone', '').strip()
        original = data.get('original', {})
        updated = data.get('updated', {})

        # Basic validation
        if not zone or not original or not updated:
            return jsonify({
                'success': False,
                'message': 'Incomplete data for modification'
            }), 400

        # Validate required fields
        required_fields = ['name', 'type', 'value']
        for field in required_fields:
            if not updated.get(field):
                return jsonify({
                    'success': False,
                    'message': f'Field {field} is required'
                }), 400

        # TTL conversion
        try:
            updated['ttl'] = int(updated.get('ttl', 3600))
        except ValueError:
            return jsonify({
                'success': False,
                'message': 'TTL must be an integer'
            }), 400

        # Modify record
        result = dns_manager.update_dns_record(zone, original, updated)
        
        if result['success']:
            logger.info(f"Record modified successfully: {original} -> {updated}")
            return jsonify(result)
        else:
            logger.warning(f"Record modification failed: {result['message']}")
            return jsonify(result), 400

    except Exception as e:
        logger.error(f"Error modifying record: {e}")
        return jsonify({
            'success': False,
            'message': f'Technical error during modification: {str(e)}'
        }), 500


@app.errorhandler(404)
def not_found(error):
    return render_template('index.html', zones=[], error="Page not found"), 404


@app.errorhandler(500)
def internal_error(error):
    return render_template('index.html', zones=[], error="Internal server error"), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)