"""Test endpoint for NCBI API key verification."""
from flask import Blueprint, jsonify, current_app
from flask_login import login_required

test_ncbi_bp = Blueprint('test_ncbi', __name__)


@test_ncbi_bp.route('/api/test-ncbi', methods=['GET'])
@login_required
def test_ncbi_api():
    """Test NCBI API key connectivity and return diagnostic info.
    
    Returns JSON with:
    - ncbi_api_key_set: bool - whether NCBI_API_KEY is configured
    - biopython_available: bool - whether BioPython can be imported
    - entrez_configured: bool - whether Entrez can be configured
    - test_search: dict - results of a test NCBI search
    - rate_limit: str - "10 req/s (with API key)" or "3 req/s (without)"
    - status: str - "ok" or "error"
    - message: str - human-readable status
    """
    import os
    import time
    
    result = {
        'status': 'ok',
        'message': 'NCBI API key is working',
        'ncbi_api_key_set': False,
        'biopython_available': False,
        'entrez_configured': False,
        'test_search': None,
        'rate_limit': '3 req/s (without API key)',
    }
    
    # Check 1: API key in config
    ncbi_key = current_app.config.get('NCBI_API_KEY', '')
    result['ncbi_api_key_set'] = bool(ncbi_key)
    
    # Check 2: BioPython
    try:
        from Bio import Entrez
        result['biopython_available'] = True
    except ImportError as e:
        result['status'] = 'error'
        result['message'] = f'BioPython not available: {e}'
        return jsonify(result), 500
    
    # Check 3: Configure Entrez
    try:
        Entrez.email = 'test@example.com'
        Entrez.api_key = ncbi_key if ncbi_key else None
        result['entrez_configured'] = True
        if ncbi_key:
            result['rate_limit'] = '10 req/s (with API key)'
    except Exception as e:
        result['status'] = 'error'
        result['message'] = f'Failed to configure Entrez: {e}'
        return jsonify(result), 500
    
    # Check 4: Test search
    try:
        handle = Entrez.esearch(
            db='nuccore',
            term='Gyrodactylus 18S[All Fields]',
            retmax=5
        )
        search_result = Entrez.read(handle)
        handle.close()
        
        count = int(search_result.get('Count', 0))
        ids = search_result.get('IdList', [])
        
        result['test_search'] = {
            'query': 'Gyrodactylus 18S[All Fields]',
            'total_matches': count,
            'retrieved_ids': len(ids),
            'sample_ids': ids[:3] if ids else [],
        }
        
        # Try to fetch one record if available
        if ids:
            time.sleep(0.2)  # Polite delay
            try:
                handle = Entrez.efetch(
                    db='nuccore',
                    id=ids[0],
                    rettype='fasta',
                    retmode='text'
                )
                fasta_data = handle.read()
                handle.close()
                
                lines = fasta_data.strip().split('\n')
                result['test_search']['fetch_success'] = True
                result['test_search']['sample_record'] = {
                    'id': ids[0],
                    'header': lines[0][:100] if lines else '',
                    'sequence_length': len(lines[1]) if len(lines) > 1 else 0,
                }
            except Exception as e:
                result['test_search']['fetch_success'] = False
                result['test_search']['fetch_error'] = str(e)
    
    except Exception as e:
        result['status'] = 'error'
        result['message'] = f'NCBI search failed: {e}'
        result['test_search'] = {'error': str(e)}
        return jsonify(result), 500
    
    return jsonify(result), 200

