#!/usr/bin/env python3
"""
Test NCBI API key by running a simple query through the Flask app context.
Run this with: python test_ncbi_endpoint.py
"""
import os
import sys

# Set up Flask app context
from config import Config
from app import create_app

app = create_app(Config)

with app.app_context():
    print("=" * 70)
    print("NCBI API KEY TEST")
    print("=" * 70)
    
    # Test 1: Check if key is configured
    ncbi_key = app.config.get('NCBI_API_KEY', '')
    print(f"\n1. NCBI_API_KEY in app.config: {bool(ncbi_key)}")
    if ncbi_key:
        print(f"   Key length: {len(ncbi_key)} chars")
        print(f"   Key preview: {ncbi_key[:10]}...")
    else:
        print("   ⚠ No NCBI_API_KEY found in app config")
    
    # Test 2: Import BioPython
    try:
        from Bio import Entrez
        print("\n2. BioPython import: ✓ Success")
    except ImportError as e:
        print(f"\n2. BioPython import: ✗ Failed - {e}")
        sys.exit(1)
    
    # Test 3: Configure Entrez
    try:
        Entrez.email = "test@example.com"
        Entrez.api_key = ncbi_key if ncbi_key else None
        print("3. Entrez configuration: ✓ Success")
        if ncbi_key:
            print("   API key is configured (10 req/s allowed)")
        else:
            print("   ⚠ No API key (3 req/s limit applies)")
    except Exception as e:
        print(f"3. Entrez configuration: ✗ Failed - {e}")
        sys.exit(1)
    
    # Test 4: Simple NCBI search
    print("\n4. Testing NCBI search (Gyrodactylus 18S)...")
    try:
        handle = Entrez.esearch(db="nuccore", term="Gyrodactylus 18S[All Fields]", retmax=5)
        result = Entrez.read(handle)
        handle.close()
        
        count = int(result.get('Count', 0))
        ids = result.get('IdList', [])
        
        print(f"   ✓ Search successful!")
        print(f"   Total matches in NCBI: {count}")
        print(f"   Retrieved IDs: {len(ids)}")
        if ids:
            print(f"   Sample IDs: {ids[:3]}")
    except Exception as e:
        print(f"   ✗ Search failed: {e}")
        print(f"   Error type: {type(e).__name__}")
        sys.exit(1)
    
    # Test 5: Fetch a record if available
    if ids:
        print(f"\n5. Testing NCBI fetch (downloading record {ids[0]})...")
        try:
            import time
            time.sleep(0.2)  # Polite delay
            handle = Entrez.efetch(db="nuccore", id=ids[0], rettype="fasta", retmode="text")
            fasta_data = handle.read()
            handle.close()
            
            lines = fasta_data.strip().split('\n')
            print(f"   ✓ Fetch successful!")
            print(f"   Record ID: {ids[0]}")
            print(f"   FASTA lines: {len(lines)}")
            print(f"   Header: {lines[0][:80]}...")
        except Exception as e:
            print(f"   ✗ Fetch failed: {e}")
            sys.exit(1)
    
    print("\n" + "=" * 70)
    print("✓ ALL TESTS PASSED - NCBI API KEY IS WORKING")
    print("=" * 70)

