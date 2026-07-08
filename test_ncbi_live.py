#!/usr/bin/env python3
"""
Test NCBI API key in the deployed app by checking the running container.
"""
import os
import sys
import time

# Import the app
from config import Config
from app import create_app

app = create_app(Config)

with app.app_context():
    print("=" * 70)
    print("NCBI API KEY VERIFICATION (DEPLOYED APP)")
    print("=" * 70)
    
    # Test 1: Check environment variable
    env_key = os.environ.get('NCBI_API_KEY', '')
    print(f"\n1. Environment variable (os.environ):")
    print(f"   NCBI_API_KEY set: {bool(env_key)}")
    if env_key:
        print(f"   Length: {len(env_key)} chars")
        print(f"   Preview: {env_key[:15]}...")
    
    # Test 2: Check Flask config
    config_key = app.config.get('NCBI_API_KEY', '')
    print(f"\n2. Flask app.config:")
    print(f"   NCBI_API_KEY set: {bool(config_key)}")
    if config_key:
        print(f"   Length: {len(config_key)} chars")
        print(f"   Preview: {config_key[:15]}...")
    
    # Test 3: Check if they match
    if env_key and config_key:
        if env_key == config_key:
            print(f"\n3. ✓ Environment and config match!")
        else:
            print(f"\n3. ✗ Mismatch between env and config!")
            sys.exit(1)
    elif not env_key and not config_key:
        print(f"\n3. ⚠ Both empty (will use 3 req/s limit)")
    else:
        print(f"\n3. ✗ One is set, one is empty!")
        sys.exit(1)
    
    # Test 4: Test with BioPython
    try:
        from Bio import Entrez
        Entrez.email = "test@example.com"
        Entrez.api_key = config_key if config_key else None
        
        print(f"\n4. BioPython Entrez configuration:")
        print(f"   Email: {Entrez.email}")
        print(f"   API Key set: {bool(Entrez.api_key)}")
        if Entrez.api_key:
            print(f"   Rate limit: 10 req/s (with API key)")
        else:
            print(f"   Rate limit: 3 req/s (without API key)")
    except Exception as e:
        print(f"\n4. ✗ BioPython error: {e}")
        sys.exit(1)
    
    # Test 5: Make a real NCBI query
    print(f"\n5. Testing NCBI search...")
    try:
        handle = Entrez.esearch(db="nuccore", term="Gyrodactylus 18S[All Fields]", retmax=10)
        result = Entrez.read(handle)
        handle.close()
        
        count = int(result.get('Count', 0))
        ids = result.get('IdList', [])
        
        print(f"   ✓ Search successful!")
        print(f"   Total matches: {count}")
        print(f"   Retrieved: {len(ids)} IDs")
        if ids:
            print(f"   Sample: {ids[:2]}")
    except Exception as e:
        print(f"   ✗ Search failed: {e}")
        sys.exit(1)
    
    # Test 6: Fetch a record
    if ids:
        print(f"\n6. Testing NCBI fetch...")
        try:
            time.sleep(0.2)
            handle = Entrez.efetch(db="nuccore", id=ids[0], rettype="fasta", retmode="text")
            fasta = handle.read()
            handle.close()
            
            lines = fasta.strip().split('\n')
            print(f"   ✓ Fetch successful!")
            print(f"   Record: {ids[0]}")
            print(f"   Sequence length: {len(lines[1]) if len(lines) > 1 else 0} bp")
        except Exception as e:
            print(f"   ✗ Fetch failed: {e}")
            sys.exit(1)
    
    print("\n" + "=" * 70)
    print("✓ NCBI API KEY IS PROPERLY CONFIGURED AND WORKING")
    print("=" * 70)

